"""Opt-in integration: faucet-funded XRP API fees, simulated principal only.

RUN_XRPL_TESTNET=1 pytest tests/agentic/test_testnet.py -q -s
Never run the ordinary mock suite with this environment flag.
"""

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import a2a_pb2 as p
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_XRPL_TESTNET") != "1",
    reason="Requires explicit Testnet profile and dedicated faucet wallets",
)


async def test_live_a2a_paid_rfq_and_human_confirmed_simulated_order(browser, stack):
    service_tier = os.getenv("TESTNET_SERVICE_TIER", "standard")
    rfq_count = int(os.getenv("TESTNET_RFQ_COUNT", "3"))
    multiplier = 2 if service_tier == "turbo" else 1
    credential = (await browser.post("/api/agent-credentials", json={})).json()
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=100,
        headers={
            "Authorization": "Bearer " + credential["token"],
            "A2A-Version": "1.0",
        },
    ) as http:
        card = await A2ACardResolver(http, stack["api"]).get_agent_card()
        agent = ClientFactory(
            ClientConfig(
                httpx_client=http,
                streaming=True,
                supported_protocol_bindings=["JSONRPC"],
            )
        ).create(card)

        async def send(body, context=""):
            request = p.SendMessageRequest(
                message=p.Message(
                    message_id=uuid4().hex,
                    context_id=context,
                    role=p.ROLE_USER,
                    parts=[
                        p.Part(
                            data=ParseDict(body, Value()), media_type="application/json"
                        )
                    ],
                )
            )
            events = [e async for e in agent.send_message(request)]
            tid = next(e.task.id for e in events if e.HasField("task"))
            return await agent.get_task(p.GetTaskRequest(id=tid)), request

        def data(task):
            return MessageToDict(task.artifacts[0].parts[0].data)

        async def settled_task(tid):
            retries = 0
            for _ in range(180):
                t = await agent.get_task(p.GetTaskRequest(id=tid))
                d = data(t)
                if t.status.state == p.TASK_STATE_COMPLETED:
                    return t
                if t.status.state == p.TASK_STATE_FAILED:
                    raise AssertionError(d)
                if d.get("state") == "payment_unknown" and retries < 3:
                    print(
                        "Reconciling original signed payment; no new signing.",
                        flush=True,
                    )
                    await browser.post(
                        "/api/operations/" + d["operation_id"] + "/resume", json={}
                    )
                    retries += 1
                if d.get("state") == "payment_unknown" and retries == 3:
                    raise AssertionError(d)
                await asyncio.sleep(1)
            raise AssertionError("Testnet operation timed out")

        search, original = await send(
            {
                "operation": "find_fcn_quotes",
                "operation_id": uuid4().hex,
                "service_tier": service_tier,
                "preferences": {
                    "investment_term": "1bd",
                    "amount": "100.125",
                    "currency": "RLUSD",
                    "principal_protected": False,
                },
            }
        )
        sid = data(search)["operation_id"]
        assert search.status.state == p.TASK_STATE_INPUT_REQUIRED
        g = await browser.post(
            "/api/grants",
            json={
                "max_rfqs": rfq_count,
                "caller": credential["caller"],
                "job_id": sid,
                "service_tier": service_tier,
            },
        )
        g.raise_for_status()
        search = await settled_task(search.id)
        assert data(search)["output"]["quote_source"] == "precomputed_catalog"
        assert all(
            q["quote_source"] == "precomputed_catalog" and q["fixture_id"]
            for q in data(search)["output"]["quotes"]
        )
        first = data(search)["output"]["receipts"][0]
        print("RFQ receipt:", json.dumps(first), flush=True)
        assert (
            first["validated"]
            and first["network"] == "xrpl:1"
            and first["merchant_fee_drops"] == str(rfq_count * 1000 * multiplier)
            and first["quantity"] == rfq_count
            and len(data(search)["output"]["receipts"]) == 1
            and data(search)["output"]["evaluations"] == rfq_count
        )
        prepared, _ = await send(
            {
                "operation": "prepare_demo_order",
                "operation_id": uuid4().hex,
                "search_id": sid,
                "amount": "100.125",
                "currency": "RLUSD",
            },
            search.context_id,
        )
        prepared = await settled_task(prepared.id)
        intent = data(prepared)["output"]["intent"]
        order, order_request = await send(
            {
                "operation": "execute_demo_order",
                "operation_id": uuid4().hex,
                "intent_id": intent["id"],
            },
            search.context_id,
        )
        assert order.status.state == p.TASK_STATE_INPUT_REQUIRED
        # Test harness exercises the authenticated human UI's exact-intent approval boundary.
        r = await browser.post(
            "/api/intents/" + intent["id"] + "/confirm",
            json={"intent_hash": intent["intent_hash"]},
        )
        r.raise_for_status()
        assert r.json()["id"] == data(order)["operation_id"]
        order = await settled_task(order.id)
        filled = data(order)["output"]
        second = filled["payment_receipt"]
        print("Execution fee receipt:", json.dumps(second), flush=True)
        assert filled["simulated"] and filled["principal_transferred"] is False
        assert (
            second["validated"]
            and second["network"] == "xrpl:1"
            and second["merchant_fee_drops"] == "5000"
        )
        token = filled["mpt"]
        assert token["status"] == "DELIVERED" and token["simulated"] is False
        assert len(token["transactions"]) == 3
        assert all(
            tx["validated"] and tx["network"] == "xrpl:1"
            for tx in token["transactions"]
        )
        from agentic.mpt import open_spec

        async with httpx.AsyncClient(timeout=30) as ledger:
            minted = await ledger.post(
                stack["env"]["XRPL_RPC_URL"],
                json={
                    "method": "tx",
                    "params": [
                        {
                            "transaction": token["transactions"][0]["transaction"],
                            "api_version": 2,
                        }
                    ],
                },
            )
            mint = minted.json()["result"]
            metadata = mint["tx_json"]["MPTokenMetadata"]
            decoded = open_spec(
                metadata, base64.b64decode(stack["env"]["MPT_METADATA_KEY"])
            )
            assert decoded["terms"] == intent["quote"]["terms"]
            assert decoded["result"] == intent["quote"]["result"]
            assert mint["validated"] and len(bytes.fromhex(metadata)) <= 1024
            public = bytes.fromhex(metadata).decode()
            assert all(
                v not in public
                for v in [
                    "100.125",
                    "RLUSD",
                    "principal_protected",
                    *intent["quote"]["terms"]["underlyings"],
                ]
            )
            held = await ledger.post(
                stack["env"]["XRPL_RPC_URL"],
                json={
                    "method": "account_objects",
                    "params": [
                        {
                            "account": token["recipient"],
                            "ledger_index": "validated",
                            "type": "mptoken",
                        }
                    ],
                },
            )
            objects = held.json()["result"]["account_objects"]
            assert any(
                obj.get("MPTokenIssuanceID") == token["mpt_issuance_id"]
                and obj.get("MPTAmount") == "1"
                for obj in objects
            )
        print("MPT receipt:", json.dumps(token), flush=True)
        replay = [e async for e in agent.send_message(order_request)]
        assert replay[0].task.id == order.id
        sheet = await browser.get("/api/orders/" + filled["order_id"] + "/termsheet")
        assert sheet.status_code == 200, sheet.text
        assert sheet.json()["source"] == "validated_ledger"
        assert sheet.json()["terms"] == intent["quote"]["terms"]
        assert sheet.json()["proof"]["validated"] is True
        reset_body = {"reset_id": uuid4().hex}
        response = await browser.post("/api/reset", json=reset_body)
        response.raise_for_status()
        reset_id = response.json()["id"]
        for _ in range(120):
            state = (await browser.get("/api/state")).json()
            if not state["workspace"].get("reset_id"):
                break
            if state["reset"]["state"] == "needs_recovery":
                await browser.post("/api/reset", json=reset_body)
            await asyncio.sleep(1)
        assert not state["holdings"] and state["workspace"]["view"] == "questions", (
            state.get("reset")
        )
        async with httpx.AsyncClient(timeout=30) as ledger:

            async def entry(selector):
                r = await ledger.post(
                    stack["env"]["XRPL_RPC_URL"],
                    json={
                        "method": "ledger_entry",
                        "params": [
                            {**selector, "ledger_index": "validated", "api_version": 2}
                        ],
                    },
                )
                return r.json()["result"]

            cleared = await entry(
                {
                    "mptoken": {
                        "account": token["recipient"],
                        "mpt_issuance_id": token["mpt_issuance_id"],
                    }
                }
            )
            assert cleared.get("error") == "entryNotFound", cleared
            issuance = await entry({"mpt_issuance": token["mpt_issuance_id"]})
            assert (
                issuance["validated"] and issuance["node"]["OutstandingAmount"] == "0"
            )
            # Existing custody receipts from other sessions must remain untouched.
            remaining = await ledger.post(
                stack["env"]["XRPL_RPC_URL"],
                json={
                    "method": "account_objects",
                    "params": [
                        {
                            "account": token["recipient"],
                            "ledger_index": "validated",
                            "type": "mptoken",
                        }
                    ],
                },
            )
            before_other = {
                o["MPTokenIssuanceID"]
                for o in objects
                if o["MPTokenIssuanceID"] != token["mpt_issuance_id"]
            }
            after_other = {
                o["MPTokenIssuanceID"]
                for o in remaining.json()["result"]["account_objects"]
            }
            assert before_other == after_other
        async with httpx.AsyncClient(trust_env=False) as internal:
            cleanup = await internal.get(
                stack["orders"] + "/v1/resets/" + reset_id,
                headers={
                    "Authorization": "Bearer test-service",
                    "X-Session-ID": state["session"]["owner"],
                },
            )
        assert cleanup.json()["state"] == "completed"
        print(
            "MPT reset: return + unauthorize validated; holder entry absent; supply zero.",
            flush=True,
        )
        # Publish only verification outcomes, never internal receipt structures,
        # wallet addresses, identifiers or detailed transaction records.
        evidence = {
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Saved catalog discovery and human-confirmed simulated order on XRPL Testnet; receipt lifecycle verified; internal receipt details withheld.",
            "quote_source": "precomputed_catalog",
            "service_tier": service_tier,
            "rfq_fee_multiplier": multiplier,
            "request_schema": "sivax.agent-request.v4",
            "rfq_evaluations": rfq_count,
            "rfq_settlements": 1,
            "a2a_protocol": "1.0",
            "a2a_sdk": "1.2.3",
            "x402_sdk": "0.3.4",
            "risk_mode": "off",
            "caller": "Official A2A SDK deterministic integration client (not an LLM)",
            "rfq_fee_drops": first["merchant_fee_drops"],
            "order_fee_drops": second["merchant_fee_drops"],
            "api_fees_validated": True,
            "receipt_delivery_verified": True,
            "owner_document_verified": True,
            "duplicate_order_reused_original_task": True,
            "cleanup_completed": True,
            "holder_entry_removed": True,
            "outstanding_amount_after_return": issuance["node"]["OutstandingAmount"],
            "other_session_receipts_preserved": True,
        }
        if os.getenv("TESTNET_EVIDENCE_PATH"):
            path = Path(os.environ["TESTNET_EVIDENCE_PATH"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(evidence, indent=2) + "\n")
