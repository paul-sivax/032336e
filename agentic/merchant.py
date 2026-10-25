"""Internal demo merchant: saved quote discovery and simulated orders."""

import asyncio
import fcntl
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import Field

from .catalog import CatalogSearch, QuoteBook
from .config import Settings
from .domain import Investment, StrictModel, tier_multiplier
from .mpt import MPTService
from .payments import MerchantPayments
from .store import Store, digest


class ReservationRequest(StrictModel):
    search_id: str = Field(min_length=8, max_length=100)


class ResetRequest(StrictModel):
    reset_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class LeaseRequest(Investment):
    intent_id: str = Field(min_length=8, max_length=100)


class OrderRequest(LeaseRequest):
    reservation_id: str
    terms_hash: str
    intent_hash: str


def create_app(settings=None):
    s = settings or Settings()
    s.validate("orders")
    db = Store(s.data_dir / "orders.sqlite3")
    payment = MerchantPayments(s, db, "SIVAX Demo Order Execution", s.order_fee)
    discovery = MerchantPayments(s, db, "SIVAX Demo Quote Discovery", s.rfq_fee)
    book, mpt = QuoteBook(s, db), MPTService(s, db)

    @asynccontextmanager
    async def lifespan(app):
        lock = open(s.data_dir / "orders.lock", "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError(
                "Use one merchant process per data directory and dedicated wallet lane."
            ) from None
        try:
            await mpt.restore_cleanup()
            yield
        finally:
            await mpt.shutdown_cleanup()
            await payment.close()
            await discovery.close()
            lock.close()

    # This is a service-authenticated internal component, not a public pricer.
    app = FastAPI(
        title="SIVAX internal demo merchant",
        version="0.6.0",
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.db, app.state.payment = db, payment
    app.state.mpt, app.state.book = mpt, book

    def owner(request):
        if not hmac.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + s.service_key
        ):
            raise HTTPException(401, "Service authentication required.")
        who = request.headers.get("X-Session-ID", "")
        if not who or len(who) > 100:
            raise HTTPException(400, "Session scope required.")
        return who

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "kind": "merchant",
            "payment_mode": s.mode,
            "execution": "simulated",
            "mpt_mode": s.mode,
            "quote_source": "precomputed_catalog",
        }

    @app.get("/v1/operations/{key}")
    async def operation(key: str, request: Request, resource: str = "order"):
        who = owner(request)
        if resource not in {"order", "discovery"}:
            raise HTTPException(422, "Unknown resource.")
        name = payment.name if resource == "order" else discovery.name
        inv = db.get("invoice", digest([name, who, key]), who)
        if not inv:
            raise HTTPException(404, "Operation not found.")
        return {
            k: inv[k]
            for k in ("id", "state", "receipt", "result", "refund")
            if k in inv
        }

    @app.post("/v1/catalog/search")
    async def catalog_search(body: CatalogSearch, request: Request):
        who = owner(request)
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 160:
            raise HTTPException(400, "A bounded Idempotency-Key is required.")
        operation_id = digest([discovery.name, who, key])
        snapshot = book.prepare_batch(who, operation_id, body)
        invoice = db.get("invoice", operation_id, who)
        if (not invoice or invoice["state"] == "UNPAID") and snapshot[
            "created_at"
        ] + s.quote_ttl <= time.time():
            raise HTTPException(410, "This saved selection expired before payment.")
        multiplier = tier_multiplier(body.service_tier)

        async def perform(paid_key):
            # The random draw was persisted before the invoice. Paid retries reuse it.
            row = db.get("catalog_batch", paid_key, who)
            if not row:
                raise RuntimeError("Saved selection is unavailable; do not draw again.")
            await asyncio.sleep(s.catalog_latency_ms / 1000 / multiplier)
            book.publish(who, row["quotes"])
            return {
                "schema_version": "sivax.catalog-result.v1",
                "seller_id": "SIVAX",
                "quote_source": "precomputed_catalog",
                "catalog_hash": row["catalog_hash"],
                "service_tier": body.service_tier,
                "delivery_speed_multiplier": multiplier,
                "batch_id": body.batch_id,
                "status": "COMPLETED",
                "evaluation_count": body.quantity,
                "quotes": row["quotes"],
                "billing": {
                    "quantity": body.quantity,
                    "unit_fee_drops": str(s.rfq_fee * multiplier),
                    "total_fee_drops": str(s.rfq_fee * body.quantity * multiplier),
                    "settlement_count": 1,
                },
            }

        return await discovery.run(
            request,
            who,
            body.model_dump(mode="json"),
            perform,
            quantity=body.quantity,
            fee_multiplier=multiplier,
        )

    @app.get("/v1/quotes/{quote_id}")
    def quote(quote_id: str, request: Request):
        return book.quote(owner(request), quote_id)

    @app.post("/v1/quotes/{quote_id}/reservations")
    def reserve(quote_id: str, body: ReservationRequest, request: Request):
        return book.reserve(owner(request), quote_id, body.search_id)

    @app.get("/v1/reservations/{rid}")
    def get_reservation(rid: str, request: Request):
        return book.reservation(owner(request), rid)

    @app.post("/v1/reservations/{rid}/lease")
    def lease(rid: str, body: LeaseRequest, request: Request):
        return book.lease(owner(request), rid, body)

    @app.get("/v1/order-progress/{intent_id}")
    def order_progress(intent_id: str, request: Request):
        who = owner(request)
        row = db.get("mpt", digest([payment.name, who, intent_id]), who)
        return {
            "phase": row.get("phase", "preparing") if row else "payment",
            "state": row["state"] if row else "PENDING",
        }

    @app.get("/v1/holdings")
    def holdings(request: Request):
        return {"holdings": mpt.holdings(owner(request))}

    @app.get("/v1/orders/{order_id}/termsheet")
    async def termsheet(order_id: str, request: Request):
        return await mpt.termsheet(owner(request), order_id)

    @app.post("/v1/resets")
    async def reset(body: ResetRequest, request: Request):
        return mpt.begin_reset(owner(request), body.reset_id)

    @app.get("/v1/resets/{reset_id}")
    def reset_state(reset_id: str, request: Request):
        row = db.get("mpt_reset", reset_id, owner(request))
        if not row:
            raise HTTPException(404, "Reset not found.")
        return row

    @app.post("/v1/orders")
    async def order(body: OrderRequest, request: Request):
        who = owner(request)
        invoice_id = digest(
            [payment.name, who, request.headers.get("Idempotency-Key", "")]
        )
        invoice = db.get("invoice", invoice_id, who)
        # Paid recovery uses the immutable saved body and original encrypted terms.
        if not invoice or invoice["state"] == "UNPAID":
            hold = book.reservation(who, body.reservation_id)
            if (
                hold["state"] != "LEASED"
                or hold.get("intent_id") != body.intent_id
                or hold.get("amount") != body.amount
                or hold.get("currency") != body.currency
                or hold["terms_hash"] != body.terms_hash
                or hold["lease_expires_at"] <= time.time()
            ):
                raise HTTPException(410, "Order has no matching live execution lease.")
            mpt.prepare(invoice_id, who, hold["quote"], body.intent_hash)

        async def perform(key):
            old = db.get("order", key, who)
            if old:
                return old
            book.consume(who, body.reservation_id, body)
            token = await mpt.deliver(key, who)
            result = {
                "order_id": key,
                "status": "FILLED",
                "simulated": True,
                "principal_transferred": False,
                "amount": body.amount,
                "currency": body.currency,
                "seller_id": "SIVAX",
                "display_name": token["display_name"],
                "mpt": token,
                "intent_id": body.intent_id,
                "terms_hash": body.terms_hash,
                "filled_at": time.time(),
                "label": "Simulated fill",
            }
            db.put("order", key, who, result)
            return result

        return await payment.run(request, who, body.model_dump(mode="json"), perform)

    @app.get("/v1/orders/{order_id}")
    def get_order(order_id: str, request: Request):
        row = db.get("order", order_id, owner(request))
        if not row:
            raise HTTPException(404, "Order not found.")
        return row

    @app.get("/v1/tokens/{issuance_id}")
    def token_mapping(issuance_id: str, request: Request):
        who = owner(request)
        for row in db.list("mpt", who):
            if row.get("mpt_issuance_id") == issuance_id:
                return {
                    "mpt_issuance_id": issuance_id,
                    "display_name": row["display_name"],
                    "order_id": row["order_id"],
                    "status": row["state"],
                }
        raise HTTPException(404, "Token mapping not found for this session.")

    return app


def app_factory():
    return create_app()
