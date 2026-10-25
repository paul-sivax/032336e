"""Repeatable, owner-scoped demo rounds. Financial audit records are never erased."""

import asyncio
import time

from fastapi import HTTPException

from .domain import Preferences, next_pool
from .store import digest, uid


class Lifecycle:
    def workspace(self, owner, con=None):
        if con is None:
            with self.db.tx() as c:
                return self.workspace(owner, c)
        w = self.db.get("workspace", owner, owner, con)
        if w:
            return w
        jobs = self.db.list("job", owner, con)
        w = {
            "cycle_id": "initial",
            "view": "home" if jobs else "questions",
            "active_search_id": None,
            "reset_id": None,
        }
        for j in jobs:
            if (
                j.get("request", {}).get("preferences")
                and j.get("state") == "completed"
            ):
                try:
                    p = Preferences.model_validate(j["request"]["preferences"])
                except ValueError:
                    continue
                self.save_profile(owner, p, j["id"], con)
        self.db.put("workspace", owner, owner, w, con)
        return w

    def save_profile(self, owner, preferences, search_id, con=None):
        p = preferences.model_dump(mode="json")
        key = digest([owner, p])
        row = {
            "id": key,
            "preferences": p,
            "search_id": search_id,
            "saved_at": time.time(),
            "archived": False,
        }
        self.db.put("profile", key, owner, row, con)
        return row

    def live_cycle(self, owner, con=None):
        w = self.workspace(owner, con)
        if w.get("reset_id"):
            r = self.db.get("reset", w["reset_id"], owner, con)
            if r and r["state"] != "completed":
                raise HTTPException(
                    409, "Finish or resume the current reset before another operation."
                )
        return w["cycle_id"]

    def require_current(self, owner, row, con=None):
        if row.get("cycle_id", "initial") != self.live_cycle(owner, con):
            raise HTTPException(
                409,
                "This request belongs to a previous demo round. Start a fresh request.",
            )

    def round_budget(self, owner, con=None):
        cycle = self.workspace(owner, con)["cycle_id"]
        return sum(
            g["reserved_drops"]
            for g in self.db.list("grant", owner, con)
            if g.get("cycle_id", "initial") == cycle
        ) + sum(
            i.get("reserved_fee", 0)
            for i in self.db.list("intent", owner, con)
            if i.get("cycle_id", "initial") == cycle
        )

    def ensure_idle(self, owner, con):
        if any(
            j["state"] in {"queued", "running", "payment_unknown"}
            for j in self.db.list("job", owner, con)
        ) or any(
            a.get("state") in {"IN_FLIGHT", "UNKNOWN"}
            for a in self.db.list("attempt", owner, con)
        ):
            raise HTTPException(
                409,
                "Complete or reconcile the active operation before starting another round or resetting.",
            )

    def start_round(self, owner, action_id, mode, profile_id=None, search_id=None):
        key = digest([owner, action_id])
        with self.db.tx() as c:
            prior = self.db.get("round_action", key, owner, c)
            fingerprint = digest([mode, profile_id, search_id])
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This action ID belongs to another choice."
                    )
                return self.workspace(owner, c)
            self.live_cycle(owner, c)
            self.ensure_idle(owner, c)
            p = None
            if mode == "another_pool":
                search = self.db.get("job", search_id or "", owner, c)
                if (
                    not search
                    or search["state"] != "completed"
                    or not search.get("reviewed_all")
                    or search["request"]["operation"] != "find_fcn_quotes"
                ):
                    raise HTTPException(
                        409,
                        "View all products from a completed report before requesting another pool.",
                    )
                self.require_current(owner, search, c)
                p = next_pool(
                    Preferences.model_validate(search["request"]["preferences"])
                ).model_dump(mode="json")
            if mode == "saved":
                saved = self.db.get("profile", profile_id or "", owner, c)
                if not saved or saved.get("archived"):
                    raise HTTPException(404, "Saved preferences not found.")
                p = saved["preferences"]
            w = self.workspace(owner, c)
            w.update(
                cycle_id=uid(),
                view="questions",
                active_search_id=None,
                draft_preferences=p,
                round_cap_drops=self.s.session_cap,
            )
            self.db.put("workspace", owner, owner, w, c)
            self.db.put("round_action", key, owner, {"fingerprint": fingerprint}, c)
            return w

    def review_all(self, owner, search_id):
        with self.db.tx() as c:
            j = self.db.get("job", search_id, owner, c)
            if (
                not j
                or j["state"] != "completed"
                or j["request"]["operation"] != "find_fcn_quotes"
            ):
                raise HTTPException(404, "Completed report not found.")
            self.require_current(owner, j, c)
            j["reviewed_all"] = True
            self.db.put("job", search_id, owner, j, c)
            return {"reviewed_all": True}

    def show_home(self, owner):
        with self.db.tx() as c:
            w = self.workspace(owner, c)
            w["view"] = "home"
            self.db.put("workspace", owner, owner, w, c)
            return w

    def begin_reset(self, owner, request_id):
        with self.db.tx() as c:
            w = self.workspace(owner, c)
            key = (
                request_id
                if request_id == w.get("reset_id")
                else digest([owner, request_id])
            )
            row = self.db.get("reset", key, owner, c)
            if not row:
                self.live_cycle(owner, c)
                self.ensure_idle(owner, c)
                row = {
                    "id": key,
                    "owner": owner,
                    "state": "queued",
                    "cycle_id": w["cycle_id"],
                    "created_at": time.time(),
                }
                w.update(reset_id=key, view="reset")
                self.db.put("workspace", owner, owner, w, c)
            elif row["state"] == "completed":
                return row
            row["state"] = "queued"
            row.pop("error", None)
            self.db.put("reset", key, owner, row, c)
        self.launch_reset(key)
        return row

    def launch_reset(self, key):
        task_key = "reset:" + key
        if task_key not in self.tasks or self.tasks[task_key].done():
            self.tasks[task_key] = asyncio.create_task(self.run_reset(key))

    async def run_reset(self, key):
        row = self.db.get("reset", key)
        owner = row["owner"]
        try:
            row["state"] = "running"
            self.db.put("reset", key, owner, row)
            await self.call(owner, "/v1/resets", {"reset_id": key}, merchant="orders")
            while True:
                result = await self.call(owner, "/v1/resets/" + key, merchant="orders")
                row["progress"] = result
                self.db.put("reset", key, owner, row)
                if result["state"] == "completed":
                    break
                if result["state"] == "needs_recovery":
                    raise HTTPException(
                        503,
                        result.get(
                            "error",
                            "Resume this reset to reconcile its original transactions.",
                        ),
                    )
                await asyncio.sleep(1)
            with self.db.tx() as c:
                for p in self.db.list("profile", owner, c):
                    p["archived"] = True
                    self.db.put("profile", p["id"], owner, p, c)
                w = self.workspace(owner, c)
                w.update(
                    cycle_id=uid(),
                    view="questions",
                    active_search_id=None,
                    draft_preferences=None,
                    reset_id=None,
                    round_cap_drops=self.s.session_cap,
                )
                self.db.put("workspace", owner, owner, w, c)
                row.update(state="completed", completed_at=time.time())
                self.db.put("reset", key, owner, row, c)
        except asyncio.CancelledError:
            raise  # Persisted running reset is resumed after process restart.
        except Exception:
            row.update(
                state="needs_recovery",
                error="Cleanup is not yet confirmed. Resume this reset; original signed transactions will be reused.",
            )
            self.db.put("reset", key, owner, row)
