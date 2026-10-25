import asyncio
from uuid import uuid4

import httpx
import pytest

from agentic.config import Settings
from agentic.domain import Preferences
from agentic.merchant import create_app
from agentic.payments import Payer, PaymentUnknown, decode, encode, mock_proof
from agentic.store import Store


def batch_body(count=12):
    from agentic.catalog import CatalogSearch

    return CatalogSearch(
        batch_id=uuid4().hex,
        preferences=Preferences(
            investment_term="1bd",
            amount="250",
            currency="XRP",
            principal_protected=False,
        ),
        quantity=count,
        as_of="2026-10-24T12:00:00Z",
    ).model_dump(mode="json")


async def test_catalog_draw_is_saved_before_payment_and_replayed(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, catalog_latency_ms=0))
    body = batch_body(12)
    headers = {
        "Authorization": "Bearer local-service-only",
        "X-Session-ID": "batch-owner",
        "Idempotency-Key": uuid4().hex,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://merchant"
    ) as c:
        invalid = await c.post(
            "/v1/catalog/search", headers=headers, json={**body, "quantity": 31}
        )
        assert invalid.status_code == 422 and app.state.db.list("invoice") == []
        response = await c.post("/v1/catalog/search", headers=headers, json=body)
        assert response.status_code == 402
        draft = app.state.db.list("catalog_batch")[0]["quotes"]
        assert len(draft) == 12 and len({q["product_id"] for q in draft}) == 12
        assert app.state.db.list("quote") == []
        required = decode(response.headers["PAYMENT-REQUIRED"])["accepts"][0]
        assert required["amount"] == "12000" and required["extra"]["quantity"] == 12
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
        assert data["quotes"] == draft
        assert (
            data["quote_source"] == "precomputed_catalog"
            and data["billing"]["settlement_count"] == 1
        )
        again = await asyncio.gather(
            *(
                c.post("/v1/catalog/search", headers=headers, json=body)
                for _ in range(3)
            )
        )
        assert all(r.json() == data for r in again)
        assert (
            len(app.state.db.list("invoice")) == 1
            and len(app.state.db.list("quote")) == 12
        )
        assert (
            await c.post(
                "/v1/catalog/search",
                headers=headers,
                json={**body, "preferences": {**body["preferences"], "amount": "500"}},
            )
        ).status_code == 409


async def test_batch_lost_response_reuses_original_aggregate_proof(
    stack, tmp_path, monkeypatch
):
    settings = Settings(
        data_dir=tmp_path,
        orders_url=stack["orders"],
        service_key="test-service",
        mock_secret="test-proof",
    )
    store = Store(tmp_path / "payer.sqlite3")
    payer = Payer(settings, store)
    body = batch_body(30)
    url = stack["orders"] + "/v1/catalog/search"
    owner, key = uuid4().hex, uuid4().hex
    original = httpx.AsyncClient.post
    dropped = False

    async def lose_response(self, url, *args, **kwargs):
        nonlocal dropped
        r = await original(self, url, *args, **kwargs)
        if (
            str(url).endswith("/v1/catalog/search")
            and kwargs.get("headers", {}).get("PAYMENT-SIGNATURE")
            and r.status_code == 200
            and not dropped
        ):
            dropped = True
            raise httpx.ReadTimeout("Batch settled; response lost")
        return r

    monkeypatch.setattr(httpx.AsyncClient, "post", lose_response)
    with pytest.raises(PaymentUnknown):
        await payer.pay(url, owner, key, body, 30000)
    proof = store.list("attempt")[0]["payment_header"]
    recovered = await payer.pay(url, owner, key, body, 30000)
    assert len(recovered["quotes"]) == 30
    assert recovered["payment_receipt"]["quantity"] == 30
    assert (
        len(store.list("attempt")) == 1
        and store.list("attempt")[0]["payment_header"] == proof
    )
    stack["restart"]("orders")
    async with httpx.AsyncClient(trust_env=False) as c:
        cached = await c.post(
            url,
            headers={
                "Authorization": "Bearer test-service",
                "X-Session-ID": owner,
                "Idempotency-Key": key,
            },
            json=body,
        )
    assert cached.json() == recovered


async def test_order_cannot_change_amount_after_quote(browser):
    from test_network import search, wait_job

    job, _ = await search(browser)
    r = await browser.post(
        "/api/operations",
        json={
            "operation": "prepare_demo_order",
            "operation_id": uuid4().hex,
            "search_id": job["id"],
            "amount": "200",
            "currency": "XRP",
        },
    )
    outcome = await wait_job(browser, r.json()["id"])
    assert outcome["state"] == "failed" and "amount" in outcome["error"].lower()
    assert (await browser.get("/api/state")).json()["intents"] == []
    changed_currency = await browser.post(
        "/api/operations",
        json={
            "operation": "prepare_demo_order",
            "operation_id": uuid4().hex,
            "search_id": job["id"],
            "amount": "100",
            "currency": "RLUSD",
        },
    )
    rejected = await wait_job(browser, changed_currency.json()["id"])
    assert rejected["state"] == "failed" and "currency" in rejected["error"]
