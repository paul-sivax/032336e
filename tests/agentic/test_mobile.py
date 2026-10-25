"""Paid service tiers and the mobile single-confirm boundary."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from test_batch import batch_body
from test_network import PREF, search, wait_job
from x402_xrpl.types import PaymentRequirements

from agentic.config import Settings
from agentic.merchant import create_app
from agentic.payments import Payer, decode, encode, mock_proof
from agentic.store import Store, digest


@pytest.mark.parametrize("tier,multiplier", [("standard", 1), ("turbo", 2)])
async def test_tier_binds_full_batch_fee_and_retry(tmp_path, tier, multiplier):
    settings = Settings(data_dir=tmp_path, catalog_latency_ms=0)
    app = create_app(settings)
    body = {**batch_body(30), "service_tier": tier}
    headers = {
        "Authorization": "Bearer local-service-only",
        "X-Session-ID": "mobile-owner",
        "Idempotency-Key": uuid4().hex,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://merchant"
    ) as c:
        response = await c.post("/v1/catalog/search", headers=headers, json=body)
        assert response.status_code == 402
        required = decode(response.headers["PAYMENT-REQUIRED"])["accepts"][0]
        assert required["amount"] == str(30000 * multiplier)
        assert required["extra"]["unitPrice"] == str(1000 * multiplier)
        assert required["extra"]["quantity"] == 30
        assert required["extra"]["bodyHash"] == digest(body)
        payer = Payer(settings, Store(tmp_path / "payer.sqlite3"))
        req = PaymentRequirements.from_dict(required)
        payer.validate_requirements(req, 30000 * multiplier, body)
        with pytest.raises(ValueError):
            payer.validate_requirements(
                req,
                30000 * multiplier,
                {**body, "service_tier": "standard" if tier == "turbo" else "turbo"},
            )
        headers["PAYMENT-SIGNATURE"] = encode(
            {
                "x402Version": 2,
                "accepted": required,
                "payload": {
                    "invoiceId": required["extra"]["invoiceId"],
                    "mockProof": mock_proof("local-mock-only", required),
                },
            }
        )
        paid = await c.post("/v1/catalog/search", headers=headers, json=body)
        assert paid.status_code == 200, paid.text
        data = paid.json()
        assert data["billing"] == {
            "quantity": 30,
            "unit_fee_drops": str(1000 * multiplier),
            "total_fee_drops": str(30000 * multiplier),
            "settlement_count": 1,
        }
        assert data["delivery_speed_multiplier"] == multiplier
        replay = await c.post("/v1/catalog/search", headers=headers, json=body)
        assert replay.json() == data
        changed = await c.post(
            "/v1/catalog/search",
            headers=headers,
            json={**body, "service_tier": "standard" if tier == "turbo" else "turbo"},
        )
        assert changed.status_code == 409
        assert len(app.state.db.list("invoice")) == 1


async def test_turbo_requires_matching_grant_and_has_full_capacity(browser):
    authorization = uuid4().hex
    grant = (
        await browser.post(
            "/api/grants",
            json={
                "max_rfqs": 30,
                "authorization_id": authorization,
                "service_tier": "turbo",
            },
        )
    ).json()
    assert grant["reserved_drops"] == 60000
    wrong = await browser.post(
        "/api/grants",
        json={
            "max_rfqs": 30,
            "authorization_id": authorization,
            "service_tier": "standard",
        },
    )
    assert wrong.status_code == 409
    request = {
        "operation": "find_fcn_quotes",
        "operation_id": uuid4().hex,
        "preferences": PREF,
        "spend_authorization_id": grant["id"],
        "service_tier": "turbo",
    }
    response = await browser.post("/api/operations", json=request)
    job = await wait_job(browser, response.json()["id"])
    assert job["state"] == "completed", job
    assert len(job["output"]["quotes"]) == 30
    assert job["receipts"][0]["merchant_fee_drops"] == "60000"
    state = (await browser.get("/api/state")).json()
    assert state["round_cap_drops"] == "200000" and state["reserved_drops"] == "60000"
    # A new round restores the standard budget. Changing the request cannot reuse an old tier grant.
    await browser.post(
        "/api/workspace/start", json={"action_id": uuid4().hex, "mode": "new"}
    )
    standard = (await browser.post("/api/grants", json={"max_rfqs": 30})).json()
    mismatch = await browser.post(
        "/api/operations",
        json={
            **request,
            "operation_id": uuid4().hex,
            "spend_authorization_id": standard["id"],
        },
    )
    assert mismatch.json()["state"] == "input_required"
    assert mismatch.json()["receipts"] == []
    assert (await browser.get("/api/state")).json()["round_cap_drops"] == "100000"


async def test_single_confirm_is_owner_scoped_idempotent_and_reports_issuance(
    browser, stack
):
    job, _ = await search(browser)
    selected = job["output"]["quotes"][1]
    selection = {
        "operation_id": uuid4().hex,
        "quote_id": selected["quote_id"],
        "amount": selected["terms"]["amount"],
        "currency": selected["terms"]["currency"],
    }
    prepared = await browser.post(
        "/api/searches/" + job["id"] + "/selection", json=selection
    )
    intent = (await wait_job(browser, prepared.json()["id"]))["output"]["intent"]
    assert not intent.get("approved_at")
    path = "/api/intents/" + intent["id"] + "/confirm"
    wrong = await browser.post(path, json={"intent_hash": "changed"})
    assert wrong.status_code == 409
    async with httpx.AsyncClient(base_url=stack["api"], trust_env=False) as other:
        session = (
            await other.post("/api/session", json={"access_key": "test-access"})
        ).json()
        other.headers["X-CSRF-Token"] = session["csrf"]
        assert (
            await other.post(
                "/api/searches/" + job["id"] + "/selection", json=selection
            )
        ).status_code == 404
        assert (
            await other.post(path, json={"intent_hash": intent["intent_hash"]})
        ).status_code == 409
    async with httpx.AsyncClient(
        base_url=stack["api"], cookies=browser.cookies, trust_env=False
    ) as no_csrf:
        assert (
            await no_csrf.post(path, json={"intent_hash": intent["intent_hash"]})
        ).status_code == 403
    replies = await asyncio.gather(
        *(
            browser.post(path, json={"intent_hash": intent["intent_hash"]})
            for _ in range(3)
        )
    )
    assert all(r.status_code == 200 for r in replies)
    assert len({r.json()["id"] for r in replies}) == 1
    phases = set()
    for _ in range(70):
        state = (await browser.get("/api/state")).json()
        phases.add(state["order_progress"]["phase"])
        if state["order_progress"]["phase"] == "complete":
            break
        await asyncio.sleep(0.04)
    assert {"issuing", "complete"} <= phases
    order = await wait_job(browser, replies[0].json()["id"])
    assert (
        order["state"] == "completed" and not order["output"]["principal_transferred"]
    )
    assert (
        await browser.post(path, json={"intent_hash": intent["intent_hash"]})
    ).json()["id"] == order["id"]
    state = (await browser.get("/api/state")).json()
    assert (
        len(
            [
                j
                for j in state["jobs"]
                if j["request"]["operation"] == "execute_demo_order"
            ]
        )
        == 1
    )
    assert state["reserved_drops"] == "8000"
    sheet = (
        await browser.get("/api/orders/" + order["output"]["order_id"] + "/termsheet")
    ).json()
    assert sheet["terms"] == selected["terms"]
