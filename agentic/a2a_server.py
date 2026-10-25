"""Official A2A Python SDK, protocol 1.0 JSON-RPC and streaming.

The SDK's LegacyRequestHandler name refers to its executor architecture, not
the wire protocol. v0.3 compatibility is explicitly disabled.
"""

import asyncio

from a2a.auth.user import User
from a2a.server.agent_execution import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import LegacyRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import TaskStore, TaskUpdater
from a2a.types import a2a_pb2 as p
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value
from pydantic import ValidationError

from .domain import AgentRequest
from .store import digest, uid

STATES = {
    "queued": p.TASK_STATE_SUBMITTED,
    "running": p.TASK_STATE_WORKING,
    "input_required": p.TASK_STATE_INPUT_REQUIRED,
    "payment_unknown": p.TASK_STATE_INPUT_REQUIRED,
    "completed": p.TASK_STATE_COMPLETED,
    "cancelled": p.TASK_STATE_CANCELED,
    "failed": p.TASK_STATE_FAILED,
}
TERMINAL = {
    p.TASK_STATE_COMPLETED,
    p.TASK_STATE_CANCELED,
    p.TASK_STATE_FAILED,
    p.TASK_STATE_REJECTED,
}


def part(data):
    return p.Part(data=ParseDict(data, Value()), media_type="application/json")


def message(text):
    return p.Message(
        message_id=uid(),
        role=p.ROLE_AGENT,
        parts=[p.Part(text=text, media_type="text/plain")],
    )


def payload(params):
    try:
        data = [
            MessageToDict(x.data) for x in params.message.parts if x.HasField("data")
        ]
        if len(data) != 1:
            raise ValueError()
        return AgentRequest.model_validate(data[0])
    except (ValidationError, ValueError):
        raise InvalidParamsError(
            message="Supply exactly one structured sivax.agent-request.v4 data part; see /api/schema."
        ) from None


class Caller(User):
    def __init__(self, name):
        self.name = name

    @property
    def is_authenticated(self):
        return True

    @property
    def user_name(self):
        return self.name


class ContextBuilder(ServerCallContextBuilder):
    def build(self, request):
        actor = request.state.agent_actor
        return ServerCallContext(
            user=Caller(actor["caller"]),
            tenant=actor["owner"],
            state={
                "owner": actor["owner"],
                "caller": actor["caller"],
                "headers": dict(request.headers),
            },
        )


class DurableTaskStore(TaskStore):
    def __init__(self, core):
        self.core, self.db = core, core.db

    def scope(self, ctx):
        return ctx.state["owner"] + ":" + ctx.user.user_name

    async def save(self, task, context):
        scope = self.scope(context)
        existing = self.db.get("a2a_task", task.id)
        if existing and existing["scope"] != scope:
            raise TaskNotFoundError()
        self.db.put(
            "a2a_task", task.id, scope, {"scope": scope, "task": MessageToDict(task)}
        )

    async def get(self, task_id, context):
        row = self.db.get("a2a_task", task_id, self.scope(context))
        if not row:
            return None
        task = ParseDict(row["task"], p.Task())
        mapping = self.db.get("a2a_link", task_id, self.scope(context))
        if mapping:
            job = self.core.get(context.state["owner"], mapping["job_id"])
            task.status.state = STATES[job["state"]]
            task.status.timestamp.FromSeconds(
                int(job.get("updated_at", job["created_at"]))
            )
            out = {
                "schema_version": "sivax.task-result.v1",
                "operation_id": job["id"],
                "state": job["state"],
                "progress": {
                    "planned": job["planned"],
                    "sent": job["sent"],
                    "quotes": len(job["quotes"]),
                },
                "approval_url": self.core.s.web_url + "/?operation=" + job["id"],
            }
            for key in ("output", "required_action", "error"):
                if key in job:
                    out[key] = job[key]
            del task.artifacts[:]
            task.artifacts.append(
                p.Artifact(
                    artifact_id=job["id"], name="SIVAX result", parts=[part(out)]
                )
            )
        return task

    async def list(self, params, context):
        rows = self.db.list("a2a_task", self.scope(context))
        tasks = [await self.get(r["task"]["id"], context) for r in rows]
        tasks = [
            t
            for t in tasks
            if (not params.context_id or t.context_id == params.context_id)
            and (not params.status or t.status.state == params.status)
            and (
                not params.HasField("status_timestamp_after")
                or t.status.timestamp.ToNanoseconds()
                >= params.status_timestamp_after.ToNanoseconds()
            )
        ]
        tasks.sort(key=lambda t: t.id)
        try:
            start = int(params.page_token or "0")
            if start < 0:
                raise ValueError()
        except ValueError:
            raise InvalidParamsError(message="Invalid page token.") from None
        size = params.page_size or 50
        if not 1 <= size <= 100:
            raise InvalidParamsError(message="pageSize must be between 1 and 100.")
        page = tasks[start : start + size]
        for task in page:
            if not params.include_artifacts:
                del task.artifacts[:]
        return p.ListTasksResponse(
            tasks=page,
            total_size=len(tasks),
            page_size=size,
            next_page_token=str(start + size) if start + size < len(tasks) else "",
        )

    async def delete(self, task_id, context):
        with self.db.tx() as c:
            c.execute(
                "DELETE FROM records WHERE kind='a2a_task' AND id=? AND owner=?",
                (task_id, self.scope(context)),
            )


class Executor(AgentExecutor):
    def __init__(self, core, store):
        self.core, self.store = core, store

    async def execute(self, context, event_queue):
        req = payload(p.SendMessageRequest(message=context.message))
        actor = context.call_context
        try:
            job = self.core.start(actor.state["owner"], actor.user.user_name, req)
        except HTTPException as e:
            raise InvalidParamsError(message=str(e.detail)) from None
        task = context.current_task or p.Task(
            id=context.task_id,
            context_id=context.context_id,
            status=p.TaskStatus(state=p.TASK_STATE_SUBMITTED),
            history=[context.message],
        )
        scope = self.store.scope(actor)
        self.core.db.put("a2a_link", task.id, scope, {"job_id": job["id"]})
        self.core.db.put("a2a_operation", job["id"], scope, {"task_id": task.id})
        await self.store.save(task, actor)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        previous = None
        while True:
            current = await self.store.get(task.id, actor)
            key = (
                current.status.state,
                self.core.get(actor.state["owner"], job["id"])["sent"],
            )
            if key != previous:
                await updater.add_artifact(
                    parts=list(current.artifacts[0].parts),
                    artifact_id=job["id"],
                    name="SIVAX result",
                    last_chunk=True,
                )
                await updater.update_status(
                    current.status.state,
                    message(
                        "SIVAX is comparing paid demo quotes."
                        if current.status.state == p.TASK_STATE_WORKING
                        else "Open SIVAX for details or required confirmation."
                    ),
                )
                previous = key
            if current.status.state in TERMINAL | {p.TASK_STATE_INPUT_REQUIRED}:
                return
            await asyncio.sleep(0.15)

    async def cancel(self, context, event_queue):
        link = self.core.db.get(
            "a2a_link", context.task_id, self.store.scope(context.call_context)
        )
        if not link:
            raise TaskNotFoundError()
        try:
            self.core.cancel(context.call_context.state["owner"], link["job_id"])
        except HTTPException as e:
            raise UnsupportedOperationError(message=str(e.detail)) from None
        # Cancellation is cooperative; retain the actual state while an in-flight fee resolves.
        task = await self.store.get(context.task_id, context.call_context)
        await event_queue.enqueue_event(task)


class Handler(LegacyRequestHandler):
    def __init__(self, core, store, card):
        super().__init__(Executor(core, store), store, card)
        self.core = core

    async def existing(self, params, ctx):
        req = payload(params)
        key = digest([ctx.state["owner"], ctx.user.user_name, req.operation_id])
        job = self.core.db.get("job", key, ctx.state["owner"])
        request_body = req.model_dump(mode="json")
        # Keep hashes stable for v4 callers that omit the optional tier.
        if "service_tier" not in req.model_fields_set:
            request_body.pop("service_tier")
        if job and job["input_hash"] != digest(request_body):
            raise InvalidParamsError(
                message="Logical operation ID was reused for different input."
            )
        link = self.core.db.get("a2a_operation", key, self.task_store.scope(ctx))
        return await self.task_store.get(link["task_id"], ctx) if link else None

    async def on_message_send(self, params, context):
        old = await self.existing(params, context)
        if old:
            return old
        return await super().on_message_send(params, context)

    async def on_message_send_stream(self, params, context):
        old = await self.existing(params, context)
        if old:
            yield old
            if old.status.state not in TERMINAL | {p.TASK_STATE_INPUT_REQUIRED}:
                async for event in self.follow(old, context):
                    yield event
            return
        async for event in super().on_message_send_stream(params, context):
            yield event

    async def follow(self, task, ctx):
        previous = None
        while True:
            task = await self.task_store.get(task.id, ctx)
            if not task:
                raise TaskNotFoundError()
            job = self.core.get(
                ctx.state["owner"],
                self.core.db.get("a2a_link", task.id, self.task_store.scope(ctx))[
                    "job_id"
                ],
            )
            stamp = (task.status.state, job["sent"], len(job["quotes"]))
            if stamp != previous:
                for artifact in task.artifacts:
                    yield p.TaskArtifactUpdateEvent(
                        task_id=task.id,
                        context_id=task.context_id,
                        artifact=artifact,
                        last_chunk=True,
                    )
                yield p.TaskStatusUpdateEvent(
                    task_id=task.id, context_id=task.context_id, status=task.status
                )
                previous = stamp
            if task.status.state in TERMINAL | {p.TASK_STATE_INPUT_REQUIRED}:
                return
            await asyncio.sleep(0.2)

    async def on_subscribe_to_task(self, params, context):
        task = await self.task_store.get(params.id, context)
        if not task:
            raise TaskNotFoundError()
        if task.status.state in TERMINAL:
            raise UnsupportedOperationError(
                message="Use GetTask for a terminal result."
            )
        yield task
        async for event in self.follow(task, context):
            yield event


def install(app, core):
    card = p.AgentCard(
        name="SIVAX Agent",
        description="FCN and CMA product specialist. Batch RFQs with exact term, amount and principal protection choice. Paid XRPL Testnet API fees; simulated products and human-approved orders.",
        version="0.4.0",
        supported_interfaces=[
            p.AgentInterface(
                url=core.s.api_url + "/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        default_input_modes=["application/json"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=p.AgentCapabilities(streaming=True, push_notifications=False),
        security_schemes={
            "bearer": p.SecurityScheme(
                http_auth_security_scheme=p.HTTPAuthSecurityScheme(
                    scheme="bearer",
                    description="Create a scoped agent credential in the SIVAX chat screen.",
                )
            )
        },
        security_requirements=[
            p.SecurityRequirement(schemes={"bearer": p.StringList()})
        ],
        skills=[
            p.AgentSkill(
                id=op,
                name=name,
                description=description,
                tags=["fcn", "cma", "testnet", "simulated"],
            )
            for op, name, description in [
                (
                    "find_fcn_quotes",
                    "Find your SIVAX product",
                    "Return all matching FCN or CMA products from the single-seller SIVAX collection using one paid RFQ batch with investment_term, amount, currency and principal_protected. One aggregate fee; human spending authorization required.",
                ),
                (
                    "prepare_demo_order",
                    "Prepare a demo order",
                    "Select a returned quote_id and create an immutable intent for the exact XRP/RLUSD amount.",
                ),
                (
                    "execute_demo_order",
                    "Execute a demo order",
                    "Simulate the investment and issue a Testnet MPT receipt to sponsored demo custody, only after exact human confirmation.",
                ),
            ]
        ],
    )
    store = DurableTaskStore(core)
    handler = Handler(core, store, card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(
            handler,
            rpc_url="/a2a",
            context_builder=ContextBuilder(),
            enable_v0_3_compat=False,
        ),
    )
    return handler
