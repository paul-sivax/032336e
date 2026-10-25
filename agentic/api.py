import asyncio
import fcntl
import hmac
import secrets
import signal
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from .a2a_server import install
from .config import Settings
from .core import Core
from .domain import AgentRequest, Investment, ServiceTier, StrictModel
from .store import Store, canonical, digest, uid


class Login(StrictModel):
    access_key: str = Field(max_length=200)


class Grant(StrictModel):
    max_rfqs: int = Field(default=36, ge=1, le=36)
    job_id: str | None = None
    caller: str = "browser"
    authorization_id: str | None = Field(default=None, min_length=8, max_length=100)
    service_tier: ServiceTier = "standard"


class Approval(StrictModel):
    intent_hash: str


class Selection(Investment):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    quote_id: str


class RoundStart(StrictModel):
    action_id: str = Field(min_length=8, max_length=100)
    mode: Literal["new", "saved", "another_pool"]
    profile_id: str | None = None
    search_id: str | None = None


class Reset(StrictModel):
    reset_id: str = Field(min_length=8, max_length=100)


def watch_shutdown(app):
    """Flag shutdown so open SSE streams end instead of holding the server open.

    uvicorn installs its signal handlers before importing the app, so chain onto them.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)

        def handler(signum, frame, previous=previous):
            app.state.stopping = True
            if callable(previous):
                previous(signum, frame)

        signal.signal(sig, handler)


def create_app(settings=None):
    s = settings or Settings()
    s.validate()
    db = Store(s.data_dir / "orchestrator.sqlite3")
    core = Core(s, db)

    @asynccontextmanager
    async def lifespan(app):
        lock = open(s.data_dir / "orchestrator.lock", "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError(
                "Demo wallet requires one orchestrator process. Stop the other instance."
            ) from None
        await core.restore()
        watch_shutdown(app)
        yield
        await core.shutdown()
        lock.close()

    app = FastAPI(title="SIVAX Agent", version="0.4.0", lifespan=lifespan)
    app.state.core, app.state.db = core, db
    app.state.stopping = False

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.path == "/a2a":
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            actor = db.get("agent", digest(token))
            if not actor or actor["expires_at"] <= time.time():
                return JSONResponse(
                    {"error": "Agent authentication required."}, status_code=401
                )
            try:
                envelope = await request.json()
                claimed = envelope.get("params", {}).get("tenant", "")
                if claimed and claimed != actor["owner"]:
                    return JSONResponse(
                        {"error": "Tenant does not match the authenticated caller."},
                        status_code=403,
                    )
            except (ValueError, AttributeError):
                return JSONResponse(
                    {"error": "Invalid JSON-RPC envelope."}, status_code=400
                )
            request.state.agent_actor = actor
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def session(request, write=False):
        token = request.cookies.get("sivax_demo", "")
        row = db.get("session", digest(token))
        if not row or row["expires_at"] <= time.time():
            raise HTTPException(401, "Start a demo session to continue.")
        if write:
            if not hmac.compare_digest(
                request.headers.get("X-CSRF-Token", ""), row["csrf"]
            ):
                raise HTTPException(403, "Invalid session confirmation.")
            origin = request.headers.get("Origin")
            if origin and origin != s.web_url:
                raise HTTPException(403, "Request origin is not allowed.")
        return row

    @app.get("/health")
    def health():
        return {"status": "ok", **s.public()}

    @app.get("/api/config")
    def config():
        return s.public()

    @app.get("/api/schema")
    def schema():
        return AgentRequest.model_json_schema()

    @app.post("/api/session")
    def login(body: Login, request: Request, response: Response):
        origin = request.headers.get("Origin")
        if origin and origin != s.web_url:
            raise HTTPException(403, "Request origin is not allowed.")
        if not hmac.compare_digest(body.access_key, s.access_key):
            raise HTTPException(401, "Incorrect demo access code.")
        token = secrets.token_urlsafe(32)
        row = {
            "owner": uid(),
            "csrf": secrets.token_urlsafe(24),
            "expires_at": time.time() + 86400,
        }
        db.put("session", digest(token), row["owner"], row)
        response.set_cookie(
            "sivax_demo",
            token,
            httponly=True,
            samesite="strict",
            secure=s.web_url.startswith("https://"),
            max_age=86400,
        )
        return row

    @app.get("/api/state")
    async def state(request: Request):
        row = session(request)
        jobs = db.list("job", row["owner"])
        intents = db.list("intent", row["owner"])
        workspace = core.workspace(row["owner"])
        holdings = await core.call(row["owner"], "/v1/holdings", merchant="orders")
        current_orders = sorted(
            (
                j
                for j in jobs
                if j.get("cycle_id", "initial") == workspace["cycle_id"]
                and j["request"]["operation"] == "execute_demo_order"
            ),
            key=lambda j: j["created_at"],
            reverse=True,
        )
        progress = None
        if current_orders:
            latest = current_orders[0]
            if latest["state"] == "completed":
                progress = {"phase": "complete", "job_id": latest["id"]}
            elif latest["state"] in {"queued", "running", "payment_unknown"}:
                try:
                    progress = {
                        **await core.call(
                            row["owner"],
                            "/v1/order-progress/" + latest["request"]["intent_id"],
                            merchant="orders",
                        ),
                        "job_id": latest["id"],
                    }
                except HTTPException:
                    progress = {"phase": "preparing", "job_id": latest["id"]}
        return {
            "session": row,
            "config": s.public(),
            "jobs": jobs,
            "intents": intents,
            "workspace": workspace,
            "profiles": [
                p for p in db.list("profile", row["owner"]) if not p.get("archived")
            ],
            "holdings": holdings["holdings"],
            "reset": db.get("reset", workspace.get("reset_id") or "", row["owner"]),
            "reserved_drops": str(core.round_budget(row["owner"])),
            "round_cap_drops": str(workspace.get("round_cap_drops", s.session_cap)),
            "order_progress": progress,
        }

    @app.post("/api/workspace/start")
    def start_round(body: RoundStart, request: Request):
        return core.start_round(
            session(request, True)["owner"],
            body.action_id,
            body.mode,
            body.profile_id,
            body.search_id,
        )

    @app.post("/api/workspace/home")
    def home(request: Request):
        return core.show_home(session(request, True)["owner"])

    @app.post("/api/operations/{key}/review-all")
    def review_all(key: str, request: Request):
        return core.review_all(session(request, True)["owner"], key)

    @app.post("/api/searches/{key}/selection")
    async def select_quote(key: str, body: Selection, request: Request):
        owner = session(request, True)["owner"]
        search = core.get(owner, key)
        core.require_current(owner, search)
        return core.start(
            owner,
            search["caller"],
            AgentRequest(
                operation="prepare_demo_order", search_id=key, **body.model_dump()
            ),
        )

    @app.post("/api/intents/{key}/confirm")
    async def confirm(key: str, body: Approval, request: Request):
        owner = session(request, True)["owner"]
        intent = core.approve(owner, key, body.intent_hash)
        existing = sorted(
            (
                j
                for j in db.list("job", owner)
                if j["request"].get("intent_id") == key and j["state"] != "failed"
            ),
            key=lambda j: j["created_at"],
        )
        if existing:
            return existing[0]
        return core.start(
            owner,
            intent["caller"],
            AgentRequest(
                operation="execute_demo_order",
                operation_id="confirm-" + key,
                intent_id=key,
            ),
        )

    @app.get("/api/orders/{order_id}/termsheet")
    async def termsheet(order_id: str, request: Request):
        return await core.call(
            session(request)["owner"],
            "/v1/orders/" + order_id + "/termsheet",
            merchant="orders",
        )

    @app.post("/api/reset")
    async def reset(body: Reset, request: Request):
        return core.begin_reset(session(request, True)["owner"], body.reset_id)

    @app.post("/api/grants")
    async def grant(body: Grant, request: Request):
        row = session(request, True)
        if body.caller != "browser" and not any(
            a["caller"] == body.caller for a in db.list("agent", row["owner"])
        ):
            raise HTTPException(404, "Caller is not connected to this session.")
        return core.grant(
            row["owner"],
            body.caller,
            body.max_rfqs,
            body.job_id,
            body.authorization_id,
            body.service_tier,
        )

    @app.post("/api/operations")
    async def operation(body: AgentRequest, request: Request):
        return core.start(session(request, True)["owner"], "browser", body)

    @app.post("/api/operations/{key}/cancel")
    def cancel(key: str, request: Request):
        return core.cancel(session(request, True)["owner"], key)

    @app.post("/api/operations/{key}/resume")
    async def resume(key: str, request: Request):
        return core.resume(session(request, True)["owner"], key)

    @app.post("/api/intents/{key}/approve")
    async def approve(key: str, body: Approval, request: Request):
        return core.approve(session(request, True)["owner"], key, body.intent_hash)

    @app.post("/api/agent-credentials")
    def credential(request: Request):
        row = session(request, True)
        token = secrets.token_urlsafe(32)
        actor = {
            "owner": row["owner"],
            "caller": "personal-assistant-" + uid()[:8],
            "expires_at": time.time() + 86400,
        }
        db.put("agent", digest(token), row["owner"], actor)
        return {
            "token": token,
            "caller": actor["caller"],
            "expires_at": actor["expires_at"],
            "agent_card_url": s.api_url + "/.well-known/agent-card.json",
        }

    @app.get("/api/events")
    async def events(request: Request):
        row = session(request)
        try:
            cursor = int(
                request.headers.get("Last-Event-ID")
                or request.query_params.get("after", "0")
            )
        except ValueError:
            raise HTTPException(400, "Invalid event cursor.") from None

        async def stream():
            nonlocal cursor
            # End on server shutdown too; an open browser tab must not block exit.
            while not app.state.stopping and not await request.is_disconnected():
                items = db.events(row["owner"], cursor)
                for e in items:
                    cursor = e["id"]
                    yield f"id: {cursor}\ndata: {canonical(e)}\n\n"
                if not items:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    install(app, core)
    return app
