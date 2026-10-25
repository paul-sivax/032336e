import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import a2a_pb2 as p
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value
from test_batch import batch_body

from agentic.config import Settings
from agentic.payments import Payer, PaymentUnknown, decode, encode, mock_proof
from agentic.store import Store

PREF = {
    "investment_term": "1bd",
    "amount": "100",
    "currency": "XRP",
    "principal_protected": False,
}


async def wait_job(browser, key):
    for _ in range(150):
        data = (await browser.get("/api/state")).json()
        job = next(j for j in data["jobs"] if j["id"] == key)
        if job["state"] not in {"queued", "running"}:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("Job did not finish: " + json.dumps(job))


async def search(browser, count=3, preferences=None):
    grant = await browser.post("/api/grants", json={"max_rfqs": count})
    grant.raise_for_status()
    body = {
        "operation": "find_fcn_quotes",
        "operation_id": uuid4().hex,
        "preferences": preferences or PREF,
        "spend_authorization_id": grant.json()["id"],
    }
    r = await browser.post("/api/operations", json=body)
    r.raise_for_status()
    return await wait_job(browser, r.json()["id"]), body


async def test_browser_search_and_duplicate_request_are_one_charge_set(browser, stack):
    job, body = await search(browser)
    assert job["state"] == "completed", job
    assert len(job["receipts"]) == 1
    assert len(job["quotes"]) == 3
    assert job["receipts"][0]["quantity"] == 3
    assert job["receipts"][0]["merchant_fee_drops"] == "3000"
    assert job["output"]["reservation"]["state"] == "HELD"
    responses = await asyncio.gather(
        *(browser.post("/api/operations", json=body) for _ in range(5))
    )
    assert {r.json()["id"] for r in responses} == {job["id"]}
    assert all(r.status_code == 200 for r in responses)
    changed = {**body, "preferences": {**PREF, "investment_term": "1y"}}
    assert (await browser.post("/api/operations", json=changed)).status_code == 409
    stack["restart"]("orders")
    stack["restart"]("api")
    recovered = await wait_job(browser, job["id"])
    assert recovered["receipts"] == job["receipts"]
    assert len((await browser.get("/api/state")).json()["jobs"]) == 1


async def test_protected_batch_keeps_protection_and_releases_unused_allowance(browser):
    job, _ = await search(browser, 30, {**PREF, "principal_protected": True})
    assert job["state"] == "completed", job
    assert len(job["quotes"]) == 12 and len(job["receipts"]) == 1
    assert job["receipts"][0]["merchant_fee_drops"] == "12000"
    assert all(q["terms"]["principal_protected"] for q in job["quotes"])
    assert (await browser.get("/api/state")).json()["reserved_drops"] == "12000"


async def test_budget_is_transactional_and_requires_csrf(browser, stack):
    rs = await asyncio.gather(
        *(browser.post("/api/grants", json={"max_rfqs": 30}) for _ in range(4))
    )
    assert sorted(r.status_code for r in rs) == [200, 200, 200, 409]
    state = (await browser.get("/api/state")).json()
    assert state["reserved_drops"] == "90000"
    assert state["round_cap_drops"] == "100000"
    async with httpx.AsyncClient(
        base_url=stack["api"], cookies=browser.cookies, trust_env=False
    ) as c:
        assert (await c.post("/api/grants", json={"max_rfqs": 1})).status_code == 403


async def test_repeated_search_authorization_reserves_budget_once(browser):
    policy = {"max_rfqs": 30, "authorization_id": uuid4().hex}
    replies = await asyncio.gather(
        *(browser.post("/api/grants", json=policy) for _ in range(4))
    )
    assert all(r.status_code == 200 for r in replies)
    assert len({r.json()["id"] for r in replies}) == 1
    assert (await browser.get("/api/state")).json()["reserved_drops"] == "30000"
    changed = await browser.post("/api/grants", json={**policy, "max_rfqs": 3})
    assert changed.status_code == 409


async def test_exact_order_approval_and_intent_single_use(browser):
    job, _ = await search(browser)
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
    prepared = await wait_job(browser, r.json()["id"])
    assert prepared["state"] == "completed", prepared
    intent = prepared["output"]["intent"]
    body = {
        "operation": "execute_demo_order",
        "operation_id": uuid4().hex,
        "intent_id": intent["id"],
    }
    pending = (await browser.post("/api/operations", json=body)).json()
    assert pending["state"] == "input_required"
    assert (
        await browser.post(
            "/api/intents/" + intent["id"] + "/approve",
            json={"intent_hash": "tampered"},
        )
    ).status_code == 409
    r = await browser.post(
        "/api/intents/" + intent["id"] + "/approve",
        json={"intent_hash": intent["intent_hash"]},
    )
    assert r.status_code == 200
    filled = await wait_job(browser, pending["id"])
    assert filled["state"] == "completed", filled
    assert (
        filled["output"]["simulated"] and not filled["output"]["principal_transferred"]
    )
    assert filled["output"]["amount"] == "100"
    assert filled["output"]["payment_receipt"]["merchant_fee_drops"] == "5000"
    retry = (await browser.post("/api/operations", json=body)).json()
    assert retry["output"] == filled["output"]
    second = (
        await browser.post(
            "/api/operations", json={**body, "operation_id": uuid4().hex}
        )
    ).json()
    assert (await wait_job(browser, second["id"]))["state"] == "failed"


async def test_x402_challenge_body_binding_and_lost_response(
    stack, tmp_path, monkeypatch
):
    settings = Settings(
        data_dir=tmp_path,
        orders_url=stack["orders"],
        service_key="test-service",
        mock_secret="test-proof",
    )
    db = Store(tmp_path / "payer.sqlite3")
    payer = Payer(settings, db)
    owner = uuid4().hex
    key = uuid4().hex
    body = batch_body(1)
    headers = {
        "Authorization": "Bearer test-service",
        "X-Session-ID": owner,
        "Idempotency-Key": key,
    }
    async with httpx.AsyncClient(trust_env=False) as c:
        response = await c.post(
            stack["orders"] + "/v1/catalog/search", headers=headers, json=body
        )
        assert response.status_code == 402 and "PAYMENT-REQUIRED" in response.headers
        required = decode(response.headers["PAYMENT-REQUIRED"])["accepts"][0]
        wrong = {**required, "network": "xrpl:0"}
        headers["PAYMENT-SIGNATURE"] = encode(
            {
                "x402Version": 2,
                "accepted": wrong,
                "payload": {
                    "invoiceId": wrong["extra"]["invoiceId"],
                    "mockProof": mock_proof("test-proof", wrong),
                },
            }
        )
        assert (
            await c.post(
                stack["orders"] + "/v1/catalog/search", headers=headers, json=body
            )
        ).status_code == 400
    original = httpx.AsyncClient.post
    dropped = False

    async def drop_once(self, url, *args, **kwargs):
        nonlocal dropped
        r = await original(self, url, *args, **kwargs)
        if (
            str(url).endswith("/v1/catalog/search")
            and kwargs.get("headers", {}).get("PAYMENT-SIGNATURE")
            and r.status_code == 200
            and not dropped
        ):
            dropped = True
            raise httpx.ReadTimeout("Lost response after settlement")
        return r

    monkeypatch.setattr(httpx.AsyncClient, "post", drop_once)
    with pytest.raises(PaymentUnknown):
        await payer.pay(stack["orders"] + "/v1/catalog/search", owner, key, body, 1000)
    assert db.list("attempt")[0]["state"] == "UNKNOWN"
    with pytest.raises(PaymentUnknown):
        await payer.pay(
            stack["orders"] + "/v1/catalog/search", owner, "another-id", body, 1000
        )
    original_proof = db.list("attempt")[0]["payment_header"]
    recovered = await payer.pay(
        stack["orders"] + "/v1/catalog/search", owner, key, body, 1000
    )
    assert recovered["payment_receipt"]["simulation"] is True
    assert (
        len(db.list("attempt")) == 1
        and db.list("attempt")[0]["payment_header"] == original_proof
    )
    async with httpx.AsyncClient(trust_env=False) as c:
        headers.pop("PAYMENT-SIGNATURE")
        assert (
            await c.post(
                stack["orders"] + "/v1/catalog/search",
                headers=headers,
                json={
                    **body,
                    "preferences": {**body["preferences"], "investment_term": "6m"},
                },
            )
        ).status_code == 409
        headers["X-Session-ID"] = "another-session"
        assert (
            await c.get(
                stack["orders"] + "/v1/quotes/" + recovered["quotes"][0]["quote_id"],
                headers=headers,
            )
        ).status_code == 404


def data(task):
    return MessageToDict(task.artifacts[0].parts[0].data)


async def test_official_a2a_client_discovery_streaming_approval_and_isolation(
    browser, stack
):
    credential = (await browser.post("/api/agent-credentials", json={})).json()
    async with httpx.AsyncClient(trust_env=False, timeout=30) as discovery:
        card = await A2ACardResolver(discovery, stack["api"]).get_agent_card()
    assert card.supported_interfaces[0].protocol_version == "1.0"
    async with httpx.AsyncClient(
        trust_env=False,
        headers={
            "Authorization": "Bearer " + credential["token"],
            "A2A-Version": "1.0",
        },
        timeout=30,
    ) as http:
        agent = ClientFactory(
            ClientConfig(
                httpx_client=http,
                streaming=True,
                supported_protocol_bindings=["JSONRPC"],
            )
        ).create(card)
        payload = {
            "operation": "find_fcn_quotes",
            "operation_id": uuid4().hex,
            "preferences": PREF,
        }
        request = p.SendMessageRequest(
            message=p.Message(
                message_id=uuid4().hex,
                role=p.ROLE_USER,
                parts=[
                    p.Part(
                        data=ParseDict(payload, Value()), media_type="application/json"
                    )
                ],
            )
        )
        events = [event async for event in agent.send_message(request)]
        task_id = next(e.task.id for e in events if e.HasField("task"))
        task = await agent.get_task(p.GetTaskRequest(id=task_id))
        assert task.status.state == p.TASK_STATE_INPUT_REQUIRED
        job_id = data(task)["operation_id"]
        assert data(task)["required_action"]["type"] == "spending_authorization"
        assert (
            await http.post(
                stack["api"] + "/api/intents/fake/approve", json={"intent_hash": "fake"}
            )
        ).status_code == 401
        grant = await browser.post(
            "/api/grants",
            json={"max_rfqs": 3, "caller": credential["caller"], "job_id": job_id},
        )
        assert grant.status_code == 200
        job = await wait_job(browser, job_id)
        assert job["state"] == "completed", job
        task = await agent.get_task(p.GetTaskRequest(id=task_id))
        assert (
            task.status.state == p.TASK_STATE_COMPLETED
            and len(data(task)["output"]["receipts"]) == 1
            and data(task)["output"]["receipts"][0]["quantity"] == 3
        )
        retry_events = [e async for e in agent.send_message(request)]
        assert retry_events[0].task.id == task_id
        listed = await agent.list_tasks(p.ListTasksRequest(include_artifacts=True))
        assert len(listed.tasks) == 1
        # Another agent in the SAME browser session has no access to the first caller's task.
        other = (await browser.post("/api/agent-credentials", json={})).json()
        async with httpx.AsyncClient(
            trust_env=False,
            headers={"Authorization": "Bearer " + other["token"], "A2A-Version": "1.0"},
            timeout=10,
        ) as other_http:
            outsider = ClientFactory(
                ClientConfig(httpx_client=other_http, streaming=False)
            ).create(card)
            with pytest.raises(Exception):
                await outsider.get_task(p.GetTaskRequest(id=task_id))
            assert not (await outsider.list_tasks(p.ListTasksRequest())).tasks


async def test_cancellation_stops_unsent_work(browser):
    grant = (await browser.post("/api/grants", json={"max_rfqs": 30})).json()
    r = await browser.post(
        "/api/operations",
        json={
            "operation": "find_fcn_quotes",
            "operation_id": uuid4().hex,
            "preferences": PREF,
            "spend_authorization_id": grant["id"],
        },
    )
    key = r.json()["id"]
    assert (
        await browser.post("/api/operations/" + key + "/cancel", json={})
    ).status_code == 200
    j = await wait_job(browser, key)
    assert j["state"] == "cancelled"
    assert len(j["receipts"]) in {0, 1}
    assert not j.get("output", {}).get("reservation")
    if j["receipts"]:
        assert j["receipts"][0]["quantity"] == 30
        assert (await browser.get("/api/state")).json()["reserved_drops"] == "30000"


async def test_independent_agent_executable_with_browser_approval(
    browser, stack, tmp_path
):
    credential = (await browser.post("/api/agent-credentials", json={})).json()
    root = Path(__file__).resolve().parents[2]
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "examples/personal-assistant-agent/agent.py"),
        "--scripted",
        "--order-amount",
        "100",
        "--checkpoint",
        str(tmp_path / "agent.json"),
        cwd=tmp_path,
        env={
            **stack["env"],
            "SIVAX_URL": stack["api"],
            "SIVAX_AGENT_TOKEN": credential["token"],
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output_task = asyncio.create_task(proc.communicate())
    approved_search = approved_order = False
    try:
        for _ in range(300):
            state = (await browser.get("/api/state")).json()
            for job in state["jobs"]:
                if (
                    job["state"] == "input_required"
                    and job.get("required_action", {}).get("type")
                    == "spending_authorization"
                    and not approved_search
                ):
                    r = await browser.post(
                        "/api/grants",
                        json={
                            "max_rfqs": 1,
                            "caller": credential["caller"],
                            "job_id": job["id"],
                        },
                    )
                    r.raise_for_status()
                    approved_search = True
            for intent in state["intents"]:
                if not approved_order:
                    r = await browser.post(
                        f"/api/intents/{intent['id']}/approve",
                        json={"intent_hash": intent["intent_hash"]},
                    )
                    r.raise_for_status()
                    approved_order = True
            if output_task.done():
                break
            await asyncio.sleep(0.1)
        output, _ = await asyncio.wait_for(output_task, 10)
        text = output.decode()
        assert proc.returncode == 0, text
        assert approved_search and approved_order
        assert (
            "SCRIPTED PROTOCOL CLIENT" in text and "Simulated order artifact:" in text
        )
        state = (await browser.get("/api/state")).json()
        assert len([r for j in state["jobs"] for r in j["receipts"]]) == 2
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()
