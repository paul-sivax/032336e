import asyncio
import time

import httpx
from fastapi import HTTPException

from .catalog import CatalogSearch
from .domain import AgentRequest, quote_count, rank, tier_multiplier
from .lifecycle import Lifecycle
from .market_calendar import now_utc, start_date
from .payments import Payer, PaymentUnknown
from .store import digest, uid

TERMINAL = {"completed", "failed", "cancelled"}


class Core(Lifecycle):
    def __init__(self, settings, store):
        self.s, self.db = settings, store
        self.payer = Payer(settings, store)
        self.tasks = {}

    def get(self, owner, key):
        r = self.db.get("job", key, owner)
        if not r:
            raise HTTPException(404, "Operation not found.")
        return r

    def save(self, j):
        j["updated_at"] = time.time()
        with self.db.tx() as c:
            existing = self.db.get("job", j["id"], j["owner"], c)
            if existing and existing.get("cancel_requested"):
                j["cancel_requested"] = True
            self.db.put("job", j["id"], j["owner"], j, c)

    def event(self, j, kind, data):
        self.db.event(j["owner"], j["id"], kind, data)

    def start(self, owner, caller, request: AgentRequest):
        body = request.model_dump(mode="json")
        if "service_tier" not in request.model_fields_set:
            body.pop("service_tier")
        key = digest([owner, caller, request.operation_id])
        with self.db.tx() as c:
            old = self.db.get("job", key, owner, c)
            if old:
                if old["input_hash"] != digest(body):
                    raise HTTPException(
                        409, "Operation ID already belongs to different input."
                    )
                return old
            cycle = self.live_cycle(owner, c)
            j = {
                "id": key,
                "owner": owner,
                "caller": caller,
                "request": body,
                "input_hash": digest(body),
                "state": "queued",
                "created_at": time.time(),
                "cycle_id": cycle,
                "payment_mode": self.s.mode,
                "risk_mode": self.s.risk_mode,
                "quote_source": "precomputed_catalog",
                "quotes": [],
                "receipts": [],
                "sent": 0,
                "planned": 0,
                "cancel_requested": False,
                "as_of": now_utc().isoformat(),
                "grant_id": request.spend_authorization_id,
            }
            if request.operation == "find_fcn_quotes":
                if not request.preferences:
                    j.update(
                        state="input_required",
                        required_action={
                            "type": "preferences",
                            "fields": [
                                "investment_term",
                                "amount",
                                "currency",
                                "principal_protected",
                            ],
                        },
                    )
                else:
                    grant = self.db.get("grant", j["grant_id"] or "", owner, c)
                    if (
                        not grant
                        or grant["caller"] != caller
                        or grant.get("job_id")
                        or grant["expires_at"] <= time.time()
                        or grant.get("cycle_id", "initial") != cycle
                        or grant.get("service_tier", "standard") != request.service_tier
                    ):
                        j.update(
                            state="input_required",
                            required_action={"type": "spending_authorization"},
                        )
                    else:
                        grant["job_id"] = key
                        self.db.put("grant", grant["id"], owner, grant, c)
            elif request.operation == "execute_demo_order":
                intent = self.db.get("intent", request.intent_id, owner, c)
                if not intent or intent["caller"] != caller:
                    raise HTTPException(404, "Order intent not found for this caller.")
                self.require_current(owner, intent, c)
                if not intent.get("approved_at"):
                    j.update(
                        state="input_required",
                        required_action={
                            "type": "human_order_approval",
                            "intent_id": intent["id"],
                        },
                    )
            self.db.put("job", key, owner, j, c)
            if request.operation == "find_fcn_quotes":
                w = self.workspace(owner, c)
                w.update(view="search", active_search_id=key, draft_preferences=None)
                self.db.put("workspace", owner, owner, w, c)
            self.db.event(
                owner,
                key,
                "task.created",
                {"caller": caller, "operation": request.operation},
                c,
            )
        if j["state"] == "queued":
            self.launch(key)
        return j

    def grant(
        self,
        owner,
        caller,
        count,
        job_id=None,
        authorization_id=None,
        service_tier="standard",
    ):
        if not 1 <= count <= self.s.max_rfqs:
            raise HTTPException(422, "RFQ count is outside the demo limit.")
        multiplier = tier_multiplier(service_tier)
        allowance = count * self.s.rfq_fee * multiplier + (
            self.s.network_fee_cap if self.s.mode == "xrpl_testnet" else 0
        )
        with self.db.tx() as c:
            cycle = self.live_cycle(owner, c)
            grant_id = digest([owner, authorization_id]) if authorization_id else uid()
            previous = self.db.get("grant", grant_id, owner, c)
            if previous:
                self.require_current(owner, previous, c)
                if (
                    previous["caller"] != caller
                    or previous["max_rfqs"] != count
                    or previous.get("requested_job_id") != job_id
                    or previous.get("service_tier", "standard") != service_tier
                ):
                    raise HTTPException(
                        409, "Authorization ID was reused for a different policy."
                    )
                return previous
            grants = self.db.list("grant", owner, c)
            used = sum(
                g["reserved_drops"]
                for g in grants
                if g.get("cycle_id", "initial") == cycle
                and (g.get("job_id") or g["expires_at"] > time.time())
            )
            orders = sum(
                i.get("reserved_fee", 0)
                for i in self.db.list("intent", owner, c)
                if i.get("cycle_id", "initial") == cycle
            )
            w = self.workspace(owner, c)
            cap = max(
                w.get("round_cap_drops", self.s.session_cap),
                self.s.session_cap * multiplier,
            )
            if (
                used + orders + allowance + self.s.order_fee + self.s.network_fee_cap
                > cap
            ):
                raise HTTPException(
                    409, "This session has insufficient remaining demo budget."
                )
            g = {
                "id": grant_id,
                "cycle_id": cycle,
                "caller": caller,
                "max_rfqs": count,
                "reserved_drops": allowance,
                "expires_at": time.time() + 900,
                "job_id": job_id,
                "requested_job_id": job_id,
                "payment_mode": self.s.mode,
                "service_tier": service_tier,
            }
            w["round_cap_drops"] = cap
            self.db.put("workspace", owner, owner, w, c)
            self.db.put("grant", g["id"], owner, g, c)
            if job_id:
                j = self.db.get("job", job_id, owner, c)
                if j:
                    self.require_current(owner, j, c)
                if (
                    not j
                    or j["caller"] != caller
                    or j["state"] != "input_required"
                    or j.get("required_action", {}).get("type")
                    != "spending_authorization"
                    or j["request"].get("service_tier", "standard") != service_tier
                ):
                    raise HTTPException(
                        409, "This task is not awaiting spending authorization."
                    )
                j.update(grant_id=g["id"], state="queued")
                j.pop("required_action", None)
                self.db.put("job", job_id, owner, j, c)
        if job_id:
            self.launch(job_id)
        return g

    def launch(self, key):
        if key in self.tasks and not self.tasks[key].done():
            return
        self.tasks[key] = asyncio.create_task(self.run(key))

    async def restore(self):
        for r in self.db.list("reset"):
            if r["state"] in {"queued", "running"}:
                self.launch_reset(r["id"])
        for j in self.db.list("job"):
            if j["state"] in {"queued", "running"}:
                if j["request"].get("schema_version") != "sivax.agent-request.v4":
                    j.update(
                        state="failed",
                        error="This saved task requires its original application version for recovery. No new payment was created.",
                    )
                    self.save(j)
                else:
                    self.launch(j["id"])

    async def shutdown(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def call(self, owner, path, body=None, merchant="orders"):
        base = self.s.orders_url
        async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
            headers = {
                "Authorization": "Bearer " + self.s.service_key,
                "X-Session-ID": owner,
            }
            r = await c.request(
                "GET" if body is None else "POST",
                base + path,
                headers=headers,
                json=body,
            )
            if r.status_code >= 400:
                raise HTTPException(
                    r.status_code, r.json().get("detail", "Merchant request failed.")
                )
            return r.json()

    async def run(self, key):
        j = self.db.get("job", key)
        if j["state"] in TERMINAL:
            return
        if (
            j["request"]["operation"] == "find_fcn_quotes"
            and j.get("quote_source") != "precomputed_catalog"
        ):
            # Never turn an old settled search into a new charge at a new resource.
            j.update(
                state="failed",
                error="This saved search requires its original application version for recovery. Start a new round only after reviewing its payment state.",
            )
            self.save(j)
            return
        if j["payment_mode"] != self.s.mode or j["risk_mode"] != self.s.risk_mode:
            j.update(
                state="failed",
                error="Configuration changed. Resume with the original payment/risk mode.",
            )
            self.save(j)
            return
        try:
            j["state"] = "running"
            self.save(j)
            req = AgentRequest.model_validate(j["request"])
            if req.operation == "find_fcn_quotes":
                await self.search(j, req)
            elif req.operation == "prepare_demo_order":
                await self.prepare(j, req)
            else:
                await self.execute(j, req)
        except PaymentUnknown as e:
            j.update(state="payment_unknown", error=str(e))
            self.event(j, "payment.unknown", {"message": str(e)})
        except HTTPException as e:
            j.update(state="failed", error=str(e.detail))
            self.event(j, "task.failed", {"message": str(e.detail)})
        except Exception:
            j.update(
                state="failed",
                error="The service could not finish this operation. Review saved payment state before trying a new operation.",
            )
            self.event(j, "task.failed", {"message": j["error"]})
        self.save(j)

    async def search(self, j, req):
        grant = self.db.get("grant", j["grant_id"], j["owner"])
        if not grant or grant["job_id"] != j["id"] or grant["caller"] != j["caller"]:
            raise HTTPException(403, "A matching spending authorization is required.")
        if grant.get("service_tier", "standard") != req.service_tier:
            raise HTTPException(
                403, "The service tier differs from the approved tariff."
            )
        multiplier = tier_multiplier(req.service_tier)
        count = quote_count(req.preferences, grant["max_rfqs"])
        j.update(planned=count, phase="writing")
        self.save(j)
        self.event(
            j,
            "batch.planned",
            {
                "rfqs": count,
                "seller_id": "SIVAX",
                "risk_check": "NOT_REQUESTED"
                if self.s.risk_mode == "off"
                else "SIMULATED_ALLOW",
            },
        )
        url = self.s.orders_url + "/v1/catalog/search"
        key = j["id"] + "-batch"
        attempt = self.db.get("attempt", digest([url, j["owner"], key]), j["owner"])
        await asyncio.sleep(0)
        if self.get(j["owner"], j["id"])["cancel_requested"] and not attempt:
            j.update(state="cancelled", cancel_requested=True)
            self.release_budget(j, grant)
            return
        if not attempt and grant["expires_at"] <= time.time():
            self.release_budget(j, grant)
            raise HTTPException(
                410, "Search authorization expired before the batch was submitted."
            )
        body = CatalogSearch(
            batch_id=req.operation_id,
            preferences=req.preferences,
            quantity=count,
            service_tier=req.service_tier,
            as_of=j["as_of"],
        ).model_dump(mode="json")
        j.update(sent=count, phase="calling")
        self.save(j)
        self.event(j, "batch.submitted", {"count": count, "settlements": 1})
        self.event(
            j,
            "payment.pending",
            {
                "fee_drops": str(self.s.rfq_fee * count * multiplier),
                "mode": self.s.mode,
            },
        )
        batch = await self.payer.pay(
            url, j["owner"], key, body, self.s.rfq_fee * count * multiplier
        )
        j.update(
            quotes=batch["quotes"],
            receipts=[batch["payment_receipt"]],
            phase="reporting",
        )
        self.save(j)
        self.event(j, "batch.received", {"count": len(j["quotes"])})
        self.event(j, "payment.settled", batch["payment_receipt"])
        if self.get(j["owner"], j["id"])["cancel_requested"]:
            j.update(state="cancelled", cancel_requested=True)
            self.release_budget(j, grant)
            self.event(j, "search.cancelled", {"paid_rfqs": len(j["quotes"])})
            return
        ranked = rank(
            [q for q in j["quotes"] if q["expires_at"] > time.time()], req.preferences
        )
        reservation = None
        for q in ranked:
            try:
                reservation = await self.call(
                    j["owner"],
                    "/v1/quotes/" + q["quote_id"] + "/reservations",
                    {"search_id": j["id"]},
                )
                reservation["quote"]["ranking"] = q["ranking"]
                break
            except HTTPException as e:
                if e.status_code not in {409, 410}:
                    raise
        j.update(
            state="completed",
            phase="ready",
            output={
                "schema_version": "sivax.quote-result.v4",
                "service_tier": req.service_tier,
                "quote_source": "precomputed_catalog",
                "preferences": req.preferences.model_dump(mode="json"),
                "quotes": ranked,
                "recommended_quote_id": ranked[0]["quote_id"] if ranked else None,
                "seller_id": "SIVAX",
                "reservation": reservation,
                "evaluations": len(j["quotes"]),
                "receipts": j["receipts"],
                "billing": batch["billing"],
                "reason": "One best match from the SIVAX collection."
                if reservation
                else "No live reservable quote remains. A new search needs authorization.",
            },
        )
        self.release_budget(j, grant)
        self.save_profile(j["owner"], req.preferences, j["id"])
        self.event(
            j,
            "search.completed",
            {"quotes": len(ranked), "reserved": bool(reservation)},
        )

    def release_budget(self, j, grant):
        # Call only once outcomes are known. UNKNOWN jobs retain the entire cap.
        grant["reserved_drops"] = sum(
            int(r["merchant_fee_drops"]) + int(r.get("network_fee_drops", 0))
            for r in j["receipts"]
        )
        self.db.put("grant", grant["id"], j["owner"], grant)

    async def prepare(self, j, req):
        existing = self.db.get("intent", j["id"], j["owner"])
        if existing:
            j.update(
                state="completed",
                output={"schema_version": "sivax.order-intent.v2", "intent": existing},
            )
            return
        search = self.get(j["owner"], req.search_id)
        self.require_current(j["owner"], search)
        if search.get("quote_source") != "precomputed_catalog":
            raise HTTPException(
                410, "Refresh your saved plan to select from the current collection."
            )
        if search["caller"] != j["caller"]:
            raise HTTPException(403, "Quote belongs to a different caller.")
        if any(
            i.get("approved_at") and i["search_id"] == search["id"]
            for i in self.db.list("intent", j["owner"])
        ):
            raise HTTPException(
                409,
                "This search already has an approved order. Start a new round for another purchase.",
            )
        chosen = req.quote_id or search.get("output", {}).get("reservation", {}).get(
            "quote_id"
        )
        q = next((q for q in search.get("quotes", []) if q["quote_id"] == chosen), None)
        if not q or search["state"] != "completed":
            raise HTTPException(
                404, "Choose a returned quote from this completed search."
            )
        if q["terms"].get("start_date") != start_date(now_utc()).isoformat():
            raise HTTPException(
                410,
                "The market session changed. Refresh your saved preferences for current dates.",
            )
        hold = await self.call(
            j["owner"], "/v1/quotes/" + chosen + "/reservations", {"search_id": j["id"]}
        )
        if hold["state"] != "HELD" or hold["expires_at"] <= time.time():
            raise HTTPException(410, "Quote hold expired or was already used.")
        q = hold["quote"]
        if req.amount != q["terms"]["amount"] or req.currency != q["terms"]["currency"]:
            raise HTTPException(
                409,
                "Amount and currency must match the quote. Start a new search to change them.",
            )
        intent = {
            "id": j["id"],
            "owner": j["owner"],
            "caller": j["caller"],
            "cycle_id": j["cycle_id"],
            "search_id": search["id"],
            "reservation_id": hold["id"],
            "quote": q,
            "amount": req.amount,
            "currency": req.currency,
            "mpt_delivery": {
                "mode": self.s.mode,
                "recipient_kind": "sponsored_demo_custody",
                "units": "1",
                "network_fee_cap_drops": str(3 * self.s.network_fee_cap),
                "sponsored": True,
            },
            "fee_drops": str(self.s.order_fee),
            "network_fee_cap_drops": str(self.s.network_fee_cap),
            "expires_at": hold["expires_at"],
            "payment_mode": self.s.mode,
        }
        intent["intent_hash"] = digest(intent)
        self.db.put("intent", intent["id"], j["owner"], intent)
        search["output"]["reservation"] = hold
        search["active_intent_id"] = intent["id"]
        self.save(search)
        j.update(
            state="completed",
            output={
                "schema_version": "sivax.order-intent.v2",
                "intent": intent,
                "required_action": {
                    "type": "human_order_approval",
                    "intent_id": intent["id"],
                },
            },
        )
        self.event(j, "order.prepared", {"intent_id": intent["id"]})

    def approve(self, owner, intent_id, intent_hash):
        with self.db.tx() as c:
            i = self.db.get("intent", intent_id, owner, c)
            if not i or i["intent_hash"] != intent_hash:
                raise HTTPException(
                    409, "Order confirmation does not match this immutable intent."
                )
            self.require_current(owner, i, c)
            search = self.db.get("job", i["search_id"], owner, c)
            if search.get("active_intent_id", i["id"]) != i["id"]:
                raise HTTPException(
                    409,
                    "A newer product selection replaced this intent. Review the latest order.",
                )
            if i.get("approved_at"):
                return i
            if (
                i["quote"]["terms"].get("start_date")
                != start_date(now_utc()).isoformat()
            ):
                raise HTTPException(
                    410,
                    "The market session changed. Refresh saved preferences before approving a new order.",
                )
            if i["expires_at"] <= time.time() or i["payment_mode"] != self.s.mode:
                raise HTTPException(410, "Intent expired or payment mode changed.")
            total = self.round_budget(owner, c)
            fee = self.s.order_fee + (
                self.s.network_fee_cap if self.s.mode == "xrpl_testnet" else 0
            )
            if total + fee > self.workspace(owner, c).get(
                "round_cap_drops", self.s.session_cap
            ):
                raise HTTPException(409, "Session budget exhausted.")
            i.update(approved_at=time.time(), reserved_fee=fee)
            self.db.put("intent", intent_id, owner, i, c)
        for j in self.db.list("job", owner):
            if (
                j["state"] == "input_required"
                and j["request"].get("intent_id") == intent_id
            ):
                j.update(state="queued")
                j.pop("required_action", None)
                self.save(j)
                self.launch(j["id"])
        return i

    async def execute(self, j, req):
        with self.db.tx() as c:
            i = self.db.get("intent", req.intent_id, j["owner"], c)
            if i:
                self.require_current(j["owner"], i, c)
            if not i or not i.get("approved_at") or i["caller"] != j["caller"]:
                raise HTTPException(403, "Human confirmation is required.")
            if i.get("execution_job") and i["execution_job"] != j["id"]:
                raise HTTPException(
                    409, "This intent already has an execution operation."
                )
            if not i.get("execution_job") and i["expires_at"] <= time.time():
                raise HTTPException(410, "Intent expired before execution.")
            if (
                not i.get("execution_job")
                and i["quote"]["terms"].get("start_date")
                != start_date(now_utc()).isoformat()
            ):
                raise HTTPException(
                    410,
                    "The market session changed before execution. Request fresh quotes.",
                )
            i["execution_job"] = j["id"]
            self.db.put("intent", i["id"], j["owner"], i, c)
        await self.call(
            j["owner"],
            "/v1/reservations/" + i["reservation_id"] + "/lease",
            {"intent_id": i["id"], "amount": i["amount"], "currency": i["currency"]},
        )
        self.event(
            j,
            "order.accepted",
            {"amount": i["amount"], "currency": i["currency"], "simulated": True},
        )
        body = {
            "intent_id": i["id"],
            "amount": i["amount"],
            "currency": i["currency"],
            "reservation_id": i["reservation_id"],
            "terms_hash": i["quote"]["terms_hash"],
            "intent_hash": i["intent_hash"],
        }
        result = await self.payer.pay(
            self.s.orders_url + "/v1/orders",
            j["owner"],
            i["id"],
            body,
            self.s.order_fee,
        )
        j.update(
            state="completed",
            output={"schema_version": "sivax.order-result.v2", **result},
            receipts=[result["payment_receipt"]],
        )
        self.event(j, "order.filled", result)

    def cancel(self, owner, key):
        with self.db.tx() as c:
            j = self.db.get("job", key, owner, c)
            if not j:
                raise HTTPException(404, "Operation not found.")
            if j["request"]["operation"] != "find_fcn_quotes" or j["state"] in TERMINAL:
                raise HTTPException(409, "This operation can no longer be cancelled.")
            j["cancel_requested"] = True
            if j["state"] == "input_required":
                j["state"] = "cancelled"
            self.db.put("job", key, owner, j, c)
        return j

    def resume(self, owner, key):
        j = self.get(owner, key)
        self.require_current(owner, j)
        if j["state"] != "payment_unknown":
            raise HTTPException(409, "Only unresolved operations can be resumed.")
        j.update(state="queued")
        self.save(j)
        self.launch(key)
        return j
