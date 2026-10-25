"""Owner-scoped on-ledger term-sheet reads and recoverable receipt cleanup."""

import asyncio
import base64
import json
import os
import time

from fastapi import HTTPException
from xrpl.models.amounts import MPTAmount
from xrpl.models.transactions import MPTokenAuthorize, Payment

from .store import digest


class MPTLifecycle:
    def holdings(self, owner):
        rows = []
        for r in self.db.list("mpt", owner):
            if r["state"] in {"DELIVERED", "SIMULATED", "RETURNED"}:
                order = self.db.get("order", r["order_id"], owner) or {}
                rows.append(
                    {
                        "order_id": r["order_id"],
                        "display_name": r["display_name"],
                        "status": r["state"],
                        "mpt_issuance_id": r.get("mpt_issuance_id"),
                        "amount": order.get("amount"),
                        "currency": order.get("currency"),
                        "created_at": r["created_at"],
                        "mpt": r.get("result"),
                        "cleanup": r.get("cleanup", []),
                    }
                )
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    async def termsheet(self, owner, order_id):
        from .mpt import open_spec

        row = self.db.get("mpt", order_id, owner)
        if not row or not row.get("result"):
            raise HTTPException(404, "Term sheet not found for this session.")
        encoded, proof = row["metadata_hex"], None
        if row["mode"] == "xrpl_testnet":
            result = await self.rpc(
                "ledger_entry",
                {"mpt_issuance": row["mpt_issuance_id"], "ledger_index": "validated"},
            )
            node = result.get("node", {})
            if (
                result.get("validated") is not True
                or node.get("Issuer") != row["issuer"]
                or node.get("MPTokenMetadata") != encoded
            ):
                raise HTTPException(
                    503,
                    "The original metadata could not be verified on the validated ledger.",
                )
            encoded = node["MPTokenMetadata"]
            proof = {
                "ledger_index": result["ledger_index"],
                "validated": True,
                "mpt_issuance_id": row["mpt_issuance_id"],
            }
        try:
            keys = json.loads(os.getenv("MPT_METADATA_KEYS_JSON", "{}"))
            key = (
                self.key
                if row["key_id"] == self.s.mpt_key_id
                else base64.b64decode(keys[row["key_id"]], validate=True)
            )
            spec = open_spec(encoded, key)
            if (
                spec["terms_hash"] != row["terms_hash"]
                or digest(spec["terms"]) != row["terms_hash"]
            ):
                raise ValueError("Term hash mismatch")
        except Exception:
            raise HTTPException(
                503,
                "The original private key is unavailable or metadata authentication failed.",
            ) from None
        return {
            "order_id": order_id,
            "display_name": row["display_name"],
            "source": "validated_ledger" if proof else "mock_encrypted_receipt",
            "proof": proof,
            "legacy_dates": "start_date" not in spec["terms"],
            **spec,
        }

    def begin_reset(self, owner, reset_id):
        with self.db.tx() as c:
            collision = self.db.get("mpt_reset", reset_id, con=c)
            if collision and collision["owner"] != owner:
                raise HTTPException(409, "This reset ID is unavailable.")
            old = self.db.get("mpt_reset", reset_id, owner, c)
            if old and old["state"] == "completed":
                return old
            if not old:
                if any(
                    r["state"] != "completed"
                    for r in self.db.list("mpt_reset", owner, c)
                ):
                    raise HTTPException(
                        409, "Resume the original reset before starting another."
                    )
                rows = self.db.list("mpt", owner, c)
                if any(
                    r["state"] == "ISSUED"
                    or (
                        not r.get("result")
                        and any(
                            a["order_id"] == r["order_id"]
                            for a in self.db.list("mpt_attempt", owner, c)
                        )
                    )
                    for r in rows
                ):
                    raise HTTPException(
                        409, "Recover the original unfinished order before resetting."
                    )
                old = {
                    "id": reset_id,
                    "owner": owner,
                    "state": "queued",
                    "order_ids": [
                        r["order_id"]
                        for r in rows
                        if r["state"] in {"DELIVERED", "SIMULATED", "RETURNED"}
                    ],
                    "completed_orders": 0,
                    "created_at": time.time(),
                }
            old.update(state="queued")
            old.pop("error", None)
            self.db.put("mpt_reset", reset_id, owner, old, c)
        self.launch_cleanup(reset_id)
        return old

    def launch_cleanup(self, key):
        if key not in self.cleanup_tasks or self.cleanup_tasks[key].done():
            self.cleanup_tasks[key] = asyncio.create_task(self.run_cleanup(key))

    async def restore_cleanup(self):
        for r in self.db.list("mpt_reset"):
            if r["state"] in {"queued", "running"}:
                self.launch_cleanup(r["id"])

    async def shutdown_cleanup(self):
        for task in self.cleanup_tasks.values():
            task.cancel()
        await asyncio.gather(*self.cleanup_tasks.values(), return_exceptions=True)

    async def run_cleanup(self, reset_id):
        reset = self.db.get("mpt_reset", reset_id)
        owner = reset["owner"]
        try:
            async with self.lock:
                reset["state"] = "running"
                self.db.put("mpt_reset", reset_id, owner, reset)
                for index, order_id in enumerate(reset["order_ids"]):
                    row = self.db.get("mpt", order_id, owner)
                    reset.update(current_order=order_id, step="return")
                    self.db.put("mpt_reset", reset_id, owner, reset)
                    if row["state"] != "CLEARED":
                        receipts = row.get("cleanup", [])
                        if row["mode"] == "xrpl_testnet":
                            if (
                                not self.issuer
                                or row["issuer"] != self.issuer.address
                                or row["recipient"] != self.holder.address
                            ):
                                raise RuntimeError(
                                    "Original custody wallets are required."
                                )
                            mid = row["mpt_issuance_id"]
                            returned = await self.transact(
                                order_id,
                                owner,
                                "return",
                                Payment(
                                    account=self.holder.address,
                                    destination=self.issuer.address,
                                    amount=MPTAmount(mpt_issuance_id=mid, value="1"),
                                    flags=0,
                                ),
                                self.holder,
                            )
                            row.update(state="RETURNED", cleanup=[returned])
                            self.db.put("mpt", order_id, owner, row)
                            reset["step"] = "unauthorize"
                            self.db.put("mpt_reset", reset_id, owner, reset)
                            unauthorized = await self.transact(
                                order_id,
                                owner,
                                "unauthorize",
                                MPTokenAuthorize(
                                    account=self.holder.address,
                                    mptoken_issuance_id=mid,
                                    flags=1,
                                ),
                                self.holder,
                            )
                            receipts = [returned, unauthorized]
                        row.update(
                            state="CLEARED", cleanup=receipts, cleared_at=time.time()
                        )
                        self.db.put("mpt", order_id, owner, row)
                    reset["completed_orders"] = index + 1
                    self.db.put("mpt_reset", reset_id, owner, reset)
                reset.update(
                    state="completed",
                    step="done",
                    completed_at=time.time(),
                    receipts=[
                        tx
                        for order_id in reset["order_ids"]
                        for tx in self.db.get("mpt", order_id, owner).get("cleanup", [])
                    ],
                )
                self.db.put("mpt_reset", reset_id, owner, reset)
        except asyncio.CancelledError:
            raise
        except Exception:
            reset.update(
                state="needs_recovery",
                error="Return or unauthorization is not yet confirmed. Resume the original reset to reconcile its saved transactions.",
            )
            self.db.put("mpt_reset", reset_id, owner, reset)
