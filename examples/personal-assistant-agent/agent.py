"""Independent agent. Its only SIVAX interface is public Agent Card + A2A.

Use --scripted for the deterministic protocol client. Without that flag, a
configured OpenAI-compatible LLM endpoint selects the discovered skill,
extracts the preferences, and summarizes the returned artifact.
"""

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import a2a_pb2 as p
from dotenv import load_dotenv
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

load_dotenv(".env.demo")


async def llm(system, user):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    model = os.environ["LLM_MODEL"]
    key = os.environ["LLM_API_KEY"]
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            base + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])


def artifact(task):
    for a in task.artifacts:
        for part in a.parts:
            if part.HasField("data"):
                return MessageToDict(part.data)
    return {}


async def delegate(client, body, context_id=None):
    request = p.SendMessageRequest(
        message=p.Message(
            message_id=uuid4().hex,
            context_id=context_id or "",
            role=p.ROLE_USER,
            parts=[
                p.Part(data=ParseDict(body, Value()), media_type="application/json")
            ],
        )
    )
    task_id = None
    async for event in client.send_message(request):
        if event.HasField("task"):
            task_id = event.task.id
        elif event.HasField("status_update"):
            task_id = event.status_update.task_id
            print(
                "A2A:", p.TaskState.Name(event.status_update.status.state), flush=True
            )
        elif event.HasField("artifact_update"):
            task_id = event.artifact_update.task_id
    if not task_id:
        raise RuntimeError("The specialist did not return a task.")
    last = None
    for _ in range(900):
        task = await client.get_task(p.GetTaskRequest(id=task_id))
        data = artifact(task)
        current = (task.status.state, data.get("required_action", {}).get("type"))
        if current != last:
            print(
                json.dumps(
                    {
                        "task_id": task_id,
                        "status": p.TaskState.Name(task.status.state),
                        "required_action": data.get("required_action"),
                        "approval_url": data.get("approval_url"),
                    }
                ),
                flush=True,
            )
            last = current
        if task.status.state == p.TASK_STATE_COMPLETED:
            return task, data
        if task.status.state in {
            p.TASK_STATE_FAILED,
            p.TASK_STATE_CANCELED,
            p.TASK_STATE_REJECTED,
        }:
            raise RuntimeError(json.dumps(data))
        if data.get("state") == "payment_unknown":
            print(
                "Reconcile the original payment in the SIVAX screen. Do not create another task.",
                flush=True,
            )
        await asyncio.sleep(1)
    raise TimeoutError(
        "Approval wait ended. GetTask can retrieve the persisted result; no payment was automatically repeated."
    )


async def run(args):
    base = os.getenv("SIVAX_URL", "http://localhost:8010")
    token = os.environ["SIVAX_AGENT_TOKEN"]
    # Discovery is free and does not send credentials to a discovered host.
    async with httpx.AsyncClient(timeout=30) as discovery:
        card = await A2ACardResolver(discovery, base).get_agent_card()
    if not card.supported_interfaces or any(
        urlparse(i.url).netloc != urlparse(base).netloc
        for i in card.supported_interfaces
    ):
        raise ValueError("Refusing to send a credential to another Agent Card origin.")
    skills = [{"id": s.id, "description": s.description} for s in card.skills]
    print("Discovered:", card.name, "skills:", [s["id"] for s in skills], flush=True)
    checkpoint = Path(args.checkpoint)
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        if saved.get("schema_version") != "sivax.agent-request.v4":
            raise ValueError(
                "This checkpoint belongs to an older schema. Recover it with the previous app version, or choose a new --checkpoint for a new v4 search."
            )
        if saved["base"] != base:
            raise ValueError("Checkpoint belongs to another server.")
    else:
        if args.scripted:
            print("SCRIPTED PROTOCOL CLIENT — no LLM is being used.", flush=True)
            choice = {
                "skill": "find_fcn_quotes",
                "preferences": {
                    "investment_term": "1bd",
                    "amount": str(args.order_amount or 100),
                    "currency": args.currency,
                    "principal_protected": False,
                },
            }
        else:
            choice = await llm(
                "You are an independent personal assistant. Respond in English JSON only. Select a discovered skill and extract the user's explicitly stated preferences. "
                "Return {skill,preferences:{investment_term,amount,currency,principal_protected}}. principal_protected is a boolean explicitly chosen by the user. "
                "investment_term is EXACT, never a maximum: 1bd,2bd,3bd,5bd,10bd,1m,3m,6m,1y. amount is a decimal STRING from 1 to 100000 with up to 6 decimal places. currency is explicitly XRP or RLUSD. SIVAX is the only seller; find one product from its equity-linked collection. "
                "If a required preference is missing, return {missing:[field names]}; never infer an amount, principal protection choice or investment term. Do not approve purchases or invent prices.",
                json.dumps({"goal": args.goal, "discovered_skills": skills}),
            )
        if choice.get("missing"):
            raise ValueError(
                "Supply these preferences in --goal: " + ", ".join(choice["missing"])
            )
        if choice.get("skill") != "find_fcn_quotes" or not any(
            s["id"] == choice["skill"] for s in skills
        ):
            raise ValueError(
                "The selected skill is not an authorized search capability."
            )
        saved = {
            "schema_version": "sivax.agent-request.v4",
            "base": base,
            "preferences": choice["preferences"],
            "search_op": uuid4().hex,
            "prepare_op": uuid4().hex,
            "execute_op": uuid4().hex,
        }
        checkpoint.write_text(json.dumps(saved, indent=2))
        checkpoint.chmod(0o600)
    async with httpx.AsyncClient(
        timeout=120, headers={"Authorization": "Bearer " + token, "A2A-Version": "1.0"}
    ) as http:
        client = ClientFactory(
            ClientConfig(
                httpx_client=http,
                streaming=True,
                supported_protocol_bindings=["JSONRPC"],
            )
        ).create(card)
        task, result = await delegate(
            client,
            {
                "schema_version": "sivax.agent-request.v4",
                "operation": "find_fcn_quotes",
                "operation_id": saved["search_op"],
                "preferences": saved["preferences"],
            },
        )
        print("Quote artifact:", json.dumps(result), flush=True)
        if not args.scripted:
            summary = await llm(
                "Summarize this specialist artifact in English JSON {summary}. Use only its exact returned facts; say simulated quotes, explain whether the simulated payoff protects principal, and distinguish API fees. Never claim a purchase has happened.",
                json.dumps(result),
            )
            print(summary["summary"], flush=True)
        if args.order_amount is None or not result.get("output", {}).get("reservation"):
            return
        if (
            args.order_amount != Decimal(saved["preferences"]["amount"])
            or args.currency != saved["preferences"]["currency"]
        ):
            raise ValueError(
                "--order-amount and --currency must match the investment quoted in the three preferences."
            )
        if args.order_amount < 1 or args.order_amount > 100000:
            raise ValueError("The demo amount must be 1–100000.")
        _, prepared = await delegate(
            client,
            {
                "operation": "prepare_demo_order",
                "operation_id": saved["prepare_op"],
                "search_id": result["operation_id"],
                "amount": str(args.order_amount),
                "currency": args.currency,
            },
            task.context_id,
        )
        intent = prepared["output"]["intent"]
        print(
            "The exact purchase amount must now be confirmed by the user in SIVAX.",
            flush=True,
        )
        _, filled = await delegate(
            client,
            {
                "operation": "execute_demo_order",
                "operation_id": saved["execute_op"],
                "intent_id": intent["id"],
            },
            task.context_id,
        )
        print("Simulated order artifact:", json.dumps(filled), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument(
        "--goal",
        default="Invest 100 XRP for exactly one business day, without principal protection.",
    )
    parser.add_argument("--order-amount", type=Decimal)
    parser.add_argument("--currency", choices=["XRP", "RLUSD"], default="XRP")
    parser.add_argument("--checkpoint", default=".demo-agent-checkpoint.json")
    asyncio.run(run(parser.parse_args()))
