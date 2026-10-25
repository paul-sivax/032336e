import base64
import json
import secrets
from datetime import date

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from xrpl.wallet import Wallet

from agentic.catalog import eligible, project
from agentic.config import Settings
from agentic.domain import Preferences
from agentic.market_calendar import pricing_time
from agentic.mpt import MPTService, open_spec, seal_spec
from agentic.store import Store, digest


def quote(currency="RLUSD"):
    p = Preferences(
        investment_term="1bd",
        amount="1234.567891",
        currency=currency,
        principal_protected=True,
    )
    return project(eligible(p)[0], p, pricing_time(date(2026, 10, 23)))


def test_encrypted_spec_roundtrip_randomness_tampering_and_size():
    q, key = quote(), secrets.token_bytes(32)
    spec = {
        "v": 1,
        "terms": q["terms"],
        "result": q["result"],
        "terms_hash": q["terms_hash"],
        "simulated": True,
    }
    code, encoded = seal_spec(spec, key, "k1")
    assert open_spec(encoded, key) == spec
    assert len(bytes.fromhex(encoded)) <= 1024
    public = bytes.fromhex(encoded).decode()
    assert all(
        secret not in public
        for secret in [
            "RLUSD",
            "1234.567891",
            "NVDA",
            "TSLA",
            "coupon",
            "principal_protected",
            q["terms_hash"],
        ]
    )
    assert code != seal_spec(spec, key, "k1")[0]
    with pytest.raises(InvalidTag):
        open_spec(encoded, secrets.token_bytes(32))
    meta = json.loads(public)
    meta["n"] = "CHANGED"
    with pytest.raises(InvalidTag):
        open_spec(json.dumps(meta).encode().hex(), key)
    with pytest.raises(ValueError, match="1024"):
        seal_spec({"large": secrets.token_hex(3000)}, key, "k1")
    meta = json.loads(public)
    meta["ai"]["kid"] = "k2"
    with pytest.raises(InvalidTag):
        open_spec(json.dumps(meta).encode().hex(), key)


def test_testnet_requires_keys_and_separate_wallet_lanes(tmp_path):
    issuer, holder, payer = (Wallet.create() for _ in range(3))
    kwargs = dict(
        mode="xrpl_testnet",
        data_dir=tmp_path,
        pay_to=issuer.address,
        seed=payer.seed,
        access_key="private-test",
        service_key="private-test",
    )
    with pytest.raises(ValueError, match="issuer"):
        Settings(**kwargs).validate("orders")
    kwargs.update(
        mpt_issuer_seed=issuer.seed,
        mpt_holder_seed=holder.seed,
        mpt_metadata_key=base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    Settings(**kwargs).validate("orders")
    with pytest.raises(ValueError, match="separate"):
        Settings(**{**kwargs, "mpt_holder_seed": payer.seed}).validate("orders")
    with pytest.raises(ValueError, match="32-byte"):
        Settings(**{**kwargs, "mpt_metadata_key": "invalid"}).validate("orders")
    store = Store(tmp_path / "key-policy.sqlite3")
    MPTService(Settings(**kwargs), store)
    rotated = {
        **kwargs,
        "mpt_metadata_key": base64.b64encode(secrets.token_bytes(32)).decode(),
    }
    with pytest.raises(ValueError, match="key ID"):
        MPTService(Settings(**rotated), store)
    MPTService(Settings(**rotated, mpt_key_id="k2"), store)


async def test_mpt_ambiguous_submission_restart_replays_original_then_delivers_once(
    tmp_path, monkeypatch
):
    issuer, holder = Wallet.create(), Wallet.create()
    s = Settings(
        mode="xrpl_testnet",
        data_dir=tmp_path,
        mpt_issuer_seed=issuer.seed,
        mpt_holder_seed=holder.seed,
        mpt_metadata_key=base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    db = Store(tmp_path / "orders.sqlite3")
    service = MPTService(s, db)
    submitted, signed = {}, []
    lost = True
    mid = "12" * 24

    async def sign(self, transaction, wallet):
        tx = {
            **transaction.to_xrpl(),
            "Fee": "10",
            "Sequence": len(signed) + 1,
            "LastLedgerSequence": 100,
        }
        record = {"tx": tx, "hash": digest(tx).upper(), "blob": digest([tx, "signed"])}
        signed.append(record)
        return record

    async def rpc(self, method, params):
        nonlocal lost
        if method == "submit":
            a = next(x for x in signed if x["blob"] == params["tx_blob"])
            submitted[a["hash"]] = a
            if lost:
                # Service dies after ledger acceptance, before the next lookup or local receipt.
                raise RuntimeError("Process interrupted after submission")
            return {"engine_result": "tesSUCCESS"}
        a = submitted.get(params["transaction"])
        if not a:
            return {"error": "txnNotFound"}
        return {
            "validated": True,
            "ledger_index": 42,
            "tx_json": a["tx"],
            "meta": {"TransactionResult": "tesSUCCESS", "mpt_issuance_id": mid},
        }

    monkeypatch.setattr(MPTService, "sign", sign)
    monkeypatch.setattr(MPTService, "rpc", rpc)
    prepared = service.prepare("order-1", "alice", quote(), "intent-1")
    with pytest.raises(RuntimeError, match="interrupted"):
        await service.deliver("order-1", "alice")
    assert len(signed) == 1 and len(db.list("mpt_attempt")) == 1
    service.prepare("order-2", "bob", quote(), "intent-2")
    with pytest.raises(RuntimeError, match="reconciliation"):
        await service.deliver("order-2", "bob")
    assert len(signed) == 1
    lost = False
    restarted = MPTService(s, Store(tmp_path / "orders.sqlite3"))
    token = await restarted.deliver("order-1", "alice")
    assert token["status"] == "DELIVERED" and token["mpt_issuance_id"] == mid
    assert len(signed) == 3 and len(submitted) == 3
    assert [a["tx"]["TransactionType"] for a in signed] == [
        "MPTokenIssuanceCreate",
        "MPTokenAuthorize",
        "Payment",
    ]
    assert signed[2]["tx"]["Amount"] == {"mpt_issuance_id": mid, "value": "1"}
    assert signed[0]["tx"]["MaximumAmount"] == "1" and signed[0]["tx"]["Flags"] == 0
    assert signed[0]["tx"]["MPTokenMetadata"] == prepared["metadata_hex"]
    assert await restarted.deliver("order-1", "alice") == token and len(signed) == 3
    with pytest.raises(RuntimeError, match="preparation"):
        await restarted.deliver("order-1", "bob")
    with pytest.raises(HTTPException):
        restarted.prepare("order-1", "alice", quote("XRP"), "changed")
    bad = {
        "validated": True,
        "ledger_index": 42,
        "tx_json": {**signed[2]["tx"], "Destination": issuer.address},
        "meta": {"TransactionResult": "tesSUCCESS"},
    }
    with pytest.raises(RuntimeError, match="differs"):
        restarted.validate_receipt(signed[2], bad)

    # A lost return response resumes the same blob after a process restart.
    lost = True
    reset = restarted.begin_reset("alice", "reset-alice")
    await restarted.cleanup_tasks[reset["id"]]
    assert db.get("mpt_reset", reset["id"], "alice")["state"] == "needs_recovery"
    assert len(signed) == 4
    with pytest.raises(HTTPException, match="current reset"):
        restarted.prepare("order-3", "alice", quote(), "intent-3")
    lost = False
    recovered = MPTService(s, Store(tmp_path / "orders.sqlite3"))
    recovered.begin_reset("alice", "reset-alice")
    await recovered.cleanup_tasks[reset["id"]]
    assert db.get("mpt_reset", reset["id"], "alice")["state"] == "completed"
    assert len(signed) == len(submitted) == 5
    assert signed[3]["tx"]["Destination"] == issuer.address
    assert signed[3]["tx"]["Account"] == holder.address
    assert signed[4]["tx"]["TransactionType"] == "MPTokenAuthorize"
    assert signed[4]["tx"]["Flags"] == 1 and "Holder" not in signed[4]["tx"]
    assert recovered.holdings("alice") == []
    assert db.get("mpt", "order-2", "bob")["state"] == "PREPARED"
    assert recovered.begin_reset("alice", "reset-alice")["state"] == "completed"
    assert len(signed) == 5
    with pytest.raises(HTTPException, match="unavailable"):
        recovered.begin_reset("bob", "reset-alice")


async def test_rlusd_order_receipt_survives_restart_and_is_owner_scoped(browser, stack):
    # Decimal RLUSD amount, receipt mapping, restart, and owner isolation.
    from test_network import search, wait_job

    job, _ = await search(
        browser,
        3,
        {
            "investment_term": "1bd",
            "amount": "250.123456",
            "currency": "RLUSD",
            "principal_protected": False,
        },
    )
    r = await browser.post(
        "/api/operations",
        json={
            "operation": "prepare_demo_order",
            "operation_id": secrets.token_hex(16),
            "search_id": job["id"],
            "amount": "250.123456",
            "currency": "RLUSD",
        },
    )
    intent = (await wait_job(browser, r.json()["id"]))["output"]["intent"]
    assert (
        intent["currency"] == "RLUSD"
        and intent["mpt_delivery"]["recipient_kind"] == "sponsored_demo_custody"
    )
    await browser.post(
        "/api/intents/" + intent["id"] + "/approve",
        json={"intent_hash": intent["intent_hash"]},
    )
    body = {
        "operation": "execute_demo_order",
        "operation_id": secrets.token_hex(16),
        "intent_id": intent["id"],
    }
    r = await browser.post("/api/operations", json=body)
    filled = await wait_job(browser, r.json()["id"])
    assert filled["state"] == "completed", filled
    token = filled["output"]["mpt"]
    assert token["simulated"] and token["mpt_issuance_id"] is None
    assert token["display_name"] == intent["quote"]["display_name"]
    stack["restart"]("orders")
    repeat = await browser.post("/api/operations", json=body)
    assert repeat.json()["output"] == filled["output"]
    assert filled["output"]["amount"] == "250.123456"
    async with httpx.AsyncClient(trust_env=False) as c:
        denied = await c.get(
            stack["orders"] + "/v1/orders/" + filled["output"]["order_id"],
            headers={
                "Authorization": "Bearer test-service",
                "X-Session-ID": "someone-else",
            },
        )
        assert denied.status_code == 404


async def test_paid_mpt_delivery_failure_recovers_without_repaying(
    stack, tmp_path, monkeypatch
):
    from agentic.catalog import CatalogSearch
    from agentic.merchant import LeaseRequest, create_app
    from agentic.payments import decode, encode, mock_proof

    s = Settings(
        data_dir=tmp_path, service_key="test-service", mock_secret="test-proof"
    )
    request_id, owner, intent = (secrets.token_hex(16) for _ in range(3))
    profile = Preferences(
        investment_term="1bd",
        amount="125.25",
        currency="RLUSD",
        principal_protected=False,
    )
    app = create_app(s)
    draft = app.state.book.prepare_batch(
        owner,
        request_id,
        CatalogSearch(
            batch_id=request_id,
            preferences=profile,
            quantity=1,
            as_of=pricing_time(date.today()),
        ),
    )
    app.state.book.publish(owner, draft["quotes"])
    q = draft["quotes"][0]
    hold = app.state.book.reserve(owner, q["quote_id"], request_id)
    lease = {"intent_id": intent, "amount": "125.25", "currency": "RLUSD"}
    app.state.book.lease(owner, hold["id"], LeaseRequest(**lease))
    headers = {"Authorization": "Bearer test-service", "X-Session-ID": owner}
    original = app.state.mpt.deliver
    first = True

    async def fail_once(*args):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("MPT service unavailable after fee settlement")
        return await original(*args)

    monkeypatch.setattr(app.state.mpt, "deliver", fail_once)
    body = {
        **lease,
        "reservation_id": hold["id"],
        "terms_hash": q["terms_hash"],
        "intent_hash": digest(lease),
    }
    headers["Idempotency-Key"] = intent
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://orders"
    ) as c:
        challenge = await c.post("/v1/orders", headers=headers, json=body)
        req = decode(challenge.headers["PAYMENT-REQUIRED"])["accepts"][0]
        headers["PAYMENT-SIGNATURE"] = encode(
            {
                "x402Version": 2,
                "accepted": req,
                "payload": {
                    "invoiceId": req["extra"]["invoiceId"],
                    "mockProof": mock_proof("test-proof", req),
                },
            }
        )
        failed = await c.post("/v1/orders", headers=headers, json=body)
        assert failed.status_code == 503
        invoice = app.state.db.list("invoice")[0]
        receipt = invoice["receipt"]
        assert (
            invoice["state"] == "SETTLED"
            and invoice["refund"]["status"] == "PENDING_REVIEW"
        )
        headers.pop("PAYMENT-SIGNATURE")
        recovered = await c.post("/v1/orders", headers=headers, json=body)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["payment_receipt"] == receipt
        assert recovered.json()["mpt"]["status"] == "SIMULATED"
        assert (
            len(app.state.db.list("invoice"))
            == len(app.state.db.list("mpt"))
            == len(app.state.db.list("order"))
            == 1
        )
        assert "refund" not in app.state.db.list("invoice")[0]
