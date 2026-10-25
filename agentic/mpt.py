"""Private metadata, durable MPT issuance and delivery on XRPL Testnet only.

One non-tradable receipt unit represents one simulated order. Principal is never
transferred. A dedicated sponsored holder is custodial; it is NOT the user's wallet.
Signed blobs are persisted before submission and never replaced automatically.
"""

import asyncio
import base64
import json
import os
import re
import secrets
import time
import zlib

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.transaction import autofill_and_sign
from xrpl.models.amounts import MPTAmount
from xrpl.models.transactions import MPTokenAuthorize, MPTokenIssuanceCreate, Payment
from xrpl.utils import encode_mptoken_metadata, validate_mptoken_metadata
from xrpl.wallet import Wallet

from .mpt_lifecycle import MPTLifecycle
from .store import canonical, digest


def seal_spec(spec, key, key_id):
    """XLS-89 compact metadata. No plaintext financial terms, IDs or lookup URL."""
    code = secrets.token_hex(16).upper()
    nonce = secrets.token_bytes(12)
    envelope = {"v": 1, "alg": "A256GCM", "kid": key_id}
    aad = canonical([code, envelope]).encode()
    ciphertext = AESGCM(key).encrypt(
        nonce, zlib.compress(canonical(spec).encode()), aad
    )
    metadata = {
        "t": code[:6],
        "n": code,
        "d": "Testnet receipt",
        "ac": "other",
        "in": "Demo issuer",
        "i": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E",
        "ai": {
            **envelope,
            "iv": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ciphertext).decode(),
        },
    }
    encoded = encode_mptoken_metadata(metadata)
    errors = validate_mptoken_metadata(encoded)
    if errors:
        raise ValueError("Encrypted MPT metadata is invalid: " + "; ".join(errors))
    return code, encoded


def open_spec(encoded, key):
    """Operator-side helper only. Never expose keys or a public decrypt endpoint."""
    metadata = json.loads(bytes.fromhex(encoded))
    a = metadata["ai"]
    if a["v"] != 1 or a["alg"] != "A256GCM":
        raise ValueError("Unsupported encrypted metadata version.")
    aad = canonical([metadata["n"], {k: a[k] for k in ("v", "alg", "kid")}]).encode()
    raw = AESGCM(key).decrypt(
        base64.b64decode(a["iv"], validate=True),
        base64.b64decode(a["ct"], validate=True),
        aad,
    )
    return json.loads(zlib.decompress(raw))


class MPTService(MPTLifecycle):
    def __init__(self, settings, store):
        self.s, self.db = settings, store
        self.lock = asyncio.Lock()
        self.cleanup_tasks = {}
        self.issuer = (
            Wallet.from_seed(settings.mpt_issuer_seed)
            if settings.mode == "xrpl_testnet"
            else None
        )
        self.holder = (
            Wallet.from_seed(settings.mpt_holder_seed)
            if settings.mode == "xrpl_testnet"
            else None
        )
        if settings.mpt_metadata_key:
            self.key = base64.b64decode(settings.mpt_metadata_key, validate=True)
        else:
            # Mock-only durable random key; no hardcoded or frontend encryption secret.
            path = settings.data_dir / "mock-mpt.key"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as file:
                    file.write(secrets.token_bytes(32))
            self.key = path.read_bytes()
        # A key ID must keep identifying the same key across restarts/rotations.
        with store.tx() as c:
            identity = {"fingerprint": digest(base64.b64encode(self.key).decode())}
            saved = store.get("mpt_key", settings.mpt_key_id, "system", c)
            if saved and saved != identity:
                raise ValueError(
                    "MPT key ID already identifies another key. Restore it or rotate with a new key ID."
                )
            store.put("mpt_key", settings.mpt_key_id, "system", identity, c)

    def prepare(self, order_id, owner, quote, intent_hash):
        fingerprint = digest([quote["terms_hash"], intent_hash, self.s.mode])
        with self.db.tx() as c:
            if any(
                r["state"] != "completed" for r in self.db.list("mpt_reset", owner, c)
            ):
                raise HTTPException(
                    409, "Finish the current reset before placing an order."
                )
            old = self.db.get("mpt", order_id, owner, c)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "MPT receipt already belongs to another order."
                    )
                return old
            spec = {
                "v": 1,
                "terms": quote["terms"],
                "result": quote["result"],
                "terms_hash": quote["terms_hash"],
                "simulated": True,
            }
            code, metadata = seal_spec(spec, self.key, self.s.mpt_key_id)
            row = {
                "order_id": order_id,
                "fingerprint": fingerprint,
                "mode": self.s.mode,
                "state": "PREPARED",
                "opaque_name": code,
                "metadata_hex": metadata,
                "display_name": quote["display_name"],
                "terms_hash": quote["terms_hash"],
                "issuer": self.issuer.address if self.issuer else "SIMULATED_ISSUER",
                "recipient": self.holder.address
                if self.holder
                else "SIMULATED_DEMO_HOLDER",
                "key_id": self.s.mpt_key_id,
                "created_at": time.time(),
            }
            self.db.put("mpt", order_id, owner, row, c)
            return row

    async def rpc(self, method, params):
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                self.s.rpc_url,
                json={"method": method, "params": [{"api_version": 2, **params}]},
            )
            r.raise_for_status()
            return r.json()["result"]

    async def sign(self, transaction, wallet):
        signed = await autofill_and_sign(
            transaction, AsyncJsonRpcClient(self.s.rpc_url), wallet
        )
        if not signed.last_ledger_sequence or int(signed.fee) > self.s.network_fee_cap:
            raise RuntimeError("MPT transaction exceeds its network fee policy.")
        return {
            "blob": signed.blob(),
            "hash": signed.get_hash(),
            "tx": signed.to_xrpl(),
        }

    def validate_receipt(self, attempt, result):
        if result.get("validated") is not True:
            return None
        meta, expected = result.get("meta", {}), attempt["tx"]
        actual = result.get("tx_json", result)
        if meta.get("TransactionResult") != "tesSUCCESS":
            raise RuntimeError(
                "Original MPT transaction failed on-ledger; operator review required."
            )
        for field, value in expected.items():
            observed = actual.get(
                field, actual.get("DeliverMax") if field == "Amount" else None
            )
            if observed != value:
                raise RuntimeError(
                    "Validated MPT transaction differs from its saved signed intent."
                )
        receipt = {
            "transaction": attempt["hash"],
            "validated": True,
            "network": "xrpl:1",
            "ledger_index": result["ledger_index"],
            "network_fee_drops": expected["Fee"],
            "transaction_type": expected["TransactionType"],
        }
        if expected["TransactionType"] == "MPTokenIssuanceCreate":
            issuance = meta.get("mpt_issuance_id", "").upper()
            if not re.fullmatch(r"[A-F0-9]{48}", issuance):
                raise RuntimeError(
                    "Validated issuance ID is missing; reconcile the original transaction."
                )
            receipt["mpt_issuance_id"] = issuance
        return receipt

    async def transact(self, order_id, owner, step, transaction, wallet):
        aid = digest([order_id, step])
        a = self.db.get("mpt_attempt", aid, owner)
        if a and a.get("receipt"):
            return a["receipt"]
        # Unknown transactions block this wallet lane, including after process restart.
        blockers = [
            x
            for x in self.db.list("mpt_attempt")
            if x["id"] != aid
            and x["account"] == wallet.address
            and not x.get("receipt")
        ]
        if blockers:
            raise RuntimeError(
                "An earlier MPT transaction needs reconciliation before this wallet can sign again."
            )
        if not a:
            a = {
                "id": aid,
                "order_id": order_id,
                "step": step,
                "account": wallet.address,
                "state": "SIGNED",
                **await self.sign(transaction, wallet),
            }
            self.db.put(
                "mpt_attempt", aid, owner, a
            )  # MUST precede network submission.
        result = await self.rpc("tx", {"transaction": a["hash"], "binary": False})
        receipt = self.validate_receipt(a, result)
        if not receipt:
            # Replay the exact blob, never autofill/re-sign an ambiguous or expired attempt.
            a["state"] = "SUBMITTED"
            self.db.put("mpt_attempt", aid, owner, a)
            try:
                await self.rpc("submit", {"tx_blob": a["blob"]})
            except (httpx.HTTPError, KeyError):
                pass
            for _ in range(12):
                await asyncio.sleep(1)
                result = await self.rpc(
                    "tx", {"transaction": a["hash"], "binary": False}
                )
                receipt = self.validate_receipt(a, result)
                if receipt:
                    break
        if not receipt:
            a["state"] = "UNKNOWN"
            self.db.put("mpt_attempt", aid, owner, a)
            raise RuntimeError(
                "MPT outcome unknown. Resume the original order; do not create another."
            )
        a.update(state="VALIDATED", receipt=receipt)
        self.db.put("mpt_attempt", aid, owner, a)
        return receipt

    async def deliver(self, order_id, owner):
        async with self.lock:
            row = self.db.get("mpt", order_id, owner)
            if not row or row["mode"] != self.s.mode:
                raise RuntimeError("Original MPT preparation is required.")
            if row.get("result"):
                return row["result"]
            row["phase"] = "issuing"
            self.db.put("mpt", order_id, owner, row)
            if self.s.mode == "mock":
                await asyncio.sleep(0.8)
                result = {
                    "status": "SIMULATED",
                    "simulated": True,
                    "mpt_issuance_id": None,
                    "transactions": [],
                }
            else:
                if (
                    row["issuer"] != self.issuer.address
                    or row["recipient"] != self.holder.address
                ):
                    raise RuntimeError(
                        "Resume MPT delivery using the original issuer and holder wallets."
                    )
                issuance = await self.transact(
                    order_id,
                    owner,
                    "create",
                    MPTokenIssuanceCreate(
                        account=self.issuer.address,
                        asset_scale=0,
                        maximum_amount="1",
                        flags=0,
                        mptoken_metadata=row["metadata_hex"],
                    ),
                    self.issuer,
                )
                mid = issuance["mpt_issuance_id"]
                row.update(state="ISSUED", mpt_issuance_id=mid, phase="authorizing")
                self.db.put("mpt", order_id, owner, row)
                authorization = await self.transact(
                    order_id,
                    owner,
                    "authorize",
                    MPTokenAuthorize(
                        account=self.holder.address, mptoken_issuance_id=mid, flags=0
                    ),
                    self.holder,
                )
                row["phase"] = "delivering"
                self.db.put("mpt", order_id, owner, row)
                delivery = await self.transact(
                    order_id,
                    owner,
                    "deliver",
                    Payment(
                        account=self.issuer.address,
                        destination=self.holder.address,
                        amount=MPTAmount(mpt_issuance_id=mid, value="1"),
                        flags=0,
                    ),
                    self.issuer,
                )
                result = {
                    "status": "DELIVERED",
                    "simulated": False,
                    "mpt_issuance_id": mid,
                    "transactions": [issuance, authorization, delivery],
                }
            result.update(
                display_name=row["display_name"],
                opaque_name=row["opaque_name"],
                recipient=row["recipient"],
                recipient_kind="sponsored_demo_custody",
                issuer=row["issuer"],
                units="1",
                asset_scale=0,
                metadata_encryption="AES-256-GCM",
                metadata_key_id=row["key_id"],
            )
            row.update(state=result["status"], result=result, phase="complete")
            self.db.put("mpt", order_id, owner, row)
            return result
