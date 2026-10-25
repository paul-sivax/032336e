import asyncio
from datetime import date, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from test_network import PREF, search, wait_job

from agentic.catalog import eligible, project
from agentic.domain import Preferences, next_pool
from agentic.market_calendar import (
    MARKET_TZ,
    maturity_date,
    preview,
    pricing_time,
    start_date,
)


def test_close_invalidates_unapproved_order(tmp_path, monkeypatch):
    from agentic.config import Settings
    from agentic.core import Core
    from agentic.store import Store

    db = Store(tmp_path / "close.sqlite3")
    core = Core(Settings(data_dir=tmp_path), db)
    db.put(
        "job",
        "search",
        "owner",
        {
            "id": "search",
            "request": {},
            "state": "completed",
            "active_intent_id": "intent",
        },
    )
    db.put(
        "intent",
        "intent",
        "owner",
        {
            "id": "intent",
            "search_id": "search",
            "intent_hash": "bound",
            "quote": {"terms": {"start_date": "2026-10-26"}},
        },
    )
    monkeypatch.setattr(
        "agentic.core.now_utc", lambda: datetime(2026, 10, 26, 16, tzinfo=MARKET_TZ)
    )
    with pytest.raises(HTTPException, match="market session changed") as caught:
        core.approve("owner", "intent", "bound")
    assert caught.value.status_code == 410
    assert not db.get("intent", "intent", "owner").get("approved_at")


def test_exchange_sessions_holidays_early_closes_and_coupon_accrual():
    saturday = datetime(2026, 10, 24, 12, tzinfo=MARKET_TZ)
    p = preview(["1bd"], saturday)
    assert p["start_date"] == "2026-10-26"
    assert p["maturities"]["1bd"] == "2026-10-27"
    q = project(eligible(Preferences(**PREF))[0], Preferences(**PREF), saturday)
    assert q["terms"]["days"] == 1  # No weekend interest before product inception.
    assert q["terms"]["valuation_date"] == "2026-10-24"
    assert start_date(datetime(2026, 10, 26, 8, tzinfo=MARKET_TZ)) == date(2026, 10, 26)
    assert start_date(datetime(2026, 10, 26, 15, 59, tzinfo=MARKET_TZ)) == date(
        2026, 10, 26
    )
    assert start_date(datetime(2026, 10, 26, 16, tzinfo=MARKET_TZ)) == date(
        2026, 10, 27
    )
    assert start_date(datetime(2026, 11, 26, 10, tzinfo=MARKET_TZ)) == date(
        2026, 11, 27
    )
    assert start_date(datetime(2026, 11, 27, 12, 59, tzinfo=MARKET_TZ)) == date(
        2026, 11, 27
    )
    assert start_date(datetime(2026, 11, 27, 13, tzinfo=MARKET_TZ)) == date(
        2026, 11, 30
    )
    assert maturity_date(date(2026, 12, 24), "1bd") == date(2026, 12, 28)


def test_multiple_distinct_equity_and_cma_collections():
    for protected, rounds in ((False, 4), (True, 3)):
        p = Preferences(**{**PREF, "principal_protected": protected})
        seen = set()
        for _ in range(rounds):
            products = [
                project(r, p, pricing_time(date(2026, 10, 24))) for r in eligible(p)
            ]
            ids = {q["product_id"] for q in products}
            assert not ids & seen
            seen |= ids
            if protected:
                assert len(products) == 12
                assert all(
                    q["terms"]["product_type"] == "cma"
                    and not q["terms"]["underlyings"]
                    for q in products
                )
            p = next_pool(p)


async def test_all_quotes_selection_review_gate_and_repeat_rounds(browser, stack):
    state = (await browser.get("/api/state")).json()
    assert state["workspace"]["view"] == "questions"
    job, _ = await search(browser, 12)
    assert len(job["output"]["quotes"]) == 12
    different = job["output"]["quotes"][-1]
    prepare = {
        "operation": "prepare_demo_order",
        "operation_id": uuid4().hex,
        "search_id": job["id"],
        "quote_id": different["quote_id"],
        "amount": "100",
        "currency": "XRP",
    }
    r = await browser.post("/api/operations", json=prepare)
    prepared = await wait_job(browser, r.json()["id"])
    assert prepared["output"]["intent"]["quote"]["quote_id"] == different["quote_id"]
    intent = prepared["output"]["intent"]
    replacement = await browser.post(
        "/api/operations",
        json={
            **prepare,
            "operation_id": uuid4().hex,
            "quote_id": job["output"]["quotes"][0]["quote_id"],
        },
    )
    assert (await wait_job(browser, replacement.json()["id"]))["state"] == "completed"
    assert (
        await browser.post(
            f"/api/intents/{intent['id']}/approve",
            json={"intent_hash": intent["intent_hash"]},
        )
    ).status_code == 409
    denied = await browser.post(
        "/api/operations",
        json={
            **prepare,
            "operation_id": uuid4().hex,
            "quote_id": "not-a-returned-quote",
        },
    )
    assert (await wait_job(browser, denied.json()["id"]))["state"] == "failed"
    body = {"action_id": uuid4().hex, "mode": "another_pool", "search_id": job["id"]}
    assert (await browser.post("/api/workspace/start", json=body)).status_code == 409
    assert (
        await browser.post(f"/api/operations/{job['id']}/review-all", json={})
    ).status_code == 200
    started = await browser.post("/api/workspace/start", json=body)
    assert started.status_code == 200
    draft = started.json()["draft_preferences"]
    assert not set(draft["asset_pool"]) & set(
        job["request"]["preferences"]["asset_pool"]
    )
    assert draft["amount"] == "100" and draft["pool_round"] == 1
    assert (await browser.get("/api/state")).json()["reserved_drops"] == "0"
    assert (
        await browser.post("/api/workspace/start", json=body)
    ).json() == started.json()
    assert (
        await browser.post(
            f"/api/intents/{intent['id']}/approve",
            json={"intent_hash": intent["intent_hash"]},
        )
    ).status_code == 409
    newer, _ = await search(browser, 12, draft)
    assert newer["id"] != job["id"] and newer["state"] == "completed"
    state = (await browser.get("/api/state")).json()
    profile = next(p for p in state["profiles"] if p["search_id"] == newer["id"])
    r = await browser.post(
        "/api/workspace/start",
        json={"action_id": uuid4().hex, "mode": "saved", "profile_id": profile["id"]},
    )
    assert r.json()["draft_preferences"] == draft
    assert (await browser.get("/api/state")).json()["reserved_drops"] == "0"
    stack["restart"]("api")
    assert (await browser.get("/api/state")).json()["workspace"] == r.json()


async def test_termsheet_holdings_reset_and_owner_isolation(browser, stack):
    job, _ = await search(browser, 3, {**PREF, "principal_protected": True})
    r = await browser.post(
        "/api/operations",
        json={
            "operation": "prepare_demo_order",
            "operation_id": uuid4().hex,
            "search_id": job["id"],
            "amount": "100",
            "currency": "XRP",
        },
    )
    intent = (await wait_job(browser, r.json()["id"]))["output"]["intent"]
    await browser.post(
        f"/api/intents/{intent['id']}/approve",
        json={"intent_hash": intent["intent_hash"]},
    )
    r = await browser.post(
        "/api/operations",
        json={
            "operation": "execute_demo_order",
            "operation_id": uuid4().hex,
            "intent_id": intent["id"],
        },
    )
    filled = await wait_job(browser, r.json()["id"])
    order = filled["output"]["order_id"]
    terms = await browser.get(f"/api/orders/{order}/termsheet")
    assert terms.status_code == 200
    assert terms.json()["terms"] == intent["quote"]["terms"]
    assert terms.json()["source"] == "mock_encrypted_receipt"
    assert "key" not in terms.json() and "metadata_hex" not in terms.json()
    state = (await browser.get("/api/state")).json()
    assert len(state["holdings"]) == 1
    async with httpx.AsyncClient(base_url=stack["api"], trust_env=False) as other:
        await other.post("/api/session", json={"access_key": "test-access"})
        assert (await other.get(f"/api/orders/{order}/termsheet")).status_code == 404
        assert (await other.get("/api/state")).json()["holdings"] == []
    reset_body = {"reset_id": uuid4().hex}
    reset = await browser.post("/api/reset", json=reset_body)
    assert reset.status_code == 200
    for _ in range(80):
        state = (await browser.get("/api/state")).json()
        if not state["workspace"].get("reset_id"):
            break
        await asyncio.sleep(0.05)
    assert state["holdings"] == [] and state["profiles"] == []
    assert state["workspace"]["view"] == "questions" and state["reserved_drops"] == "0"
    assert len(state["jobs"]) == 3  # Audit history survives cleanup.
    assert (await browser.post("/api/reset", json=reset_body)).json()[
        "state"
    ] == "completed"
    assert (await browser.get(f"/api/orders/{order}/termsheet")).json()[
        "terms"
    ] == intent["quote"]["terms"]
