"""x402 v2 exact payments using the Starter Kit-linked x402-xrpl SDK.

Application idempotency wraps SDK verification/settlement. Signed attempts are
saved before sending and NEVER re-signed on retry. An unknown attempt blocks
the dedicated wallet lane. Mock evidence is isolated from the XRPL adapter.
"""

import asyncio
import base64
import hmac
import json
import time
from hashlib import sha256, sha512

import httpx
from fastapi import HTTPException
from starlette.responses import JSONResponse
from x402_xrpl.facilitator import AsyncFacilitatorClient, FacilitatorClientOptions
from x402_xrpl.types import PaymentRequired, PaymentRequirements, ResourceInfo

from .domain import tier_multiplier
from .store import canonical, digest


def encode(value):
    return base64.b64encode(canonical(value).encode()).decode()


def decode(value):
    return json.loads(base64.b64decode(value, validate=True))


def tx_hash(blob):
    return sha512(bytes.fromhex("54584E00" + blob)).digest()[:32].hex().upper()


def mock_proof(secret, req):
    return hmac.new(secret.encode(), canonical(req).encode(), sha256).hexdigest()


class PaymentUnknown(Exception):
    pass


async def ledger_receipt(settings, attempt, expected_amount, expected_payee):
    """Positive evidence only: absence never proves safe to pay again."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            settings.rpc_url,
            json={
                "method": "tx",
                "params": [{"transaction": attempt["tx_hash"], "binary": False}],
            },
        )
        r.raise_for_status()
        result = r.json().get("result", {})
    if result.get("validated") is not True:
        return None
    tx = result.get("tx_json", result)
    meta = result.get("meta", {})
    if meta.get("TransactionResult") != "tesSUCCESS":
        raise PaymentUnknown(
            "The original transaction failed on-ledger. Operator review required; no replacement payment was created."
        )
    if (
        str(meta.get("delivered_amount")) != str(expected_amount)
        or tx.get("Destination") != expected_payee
        or tx.get("TransactionType") != "Payment"
        or str(tx.get("Amount", tx.get("DeliverMax"))) != str(expected_amount)
        or int(tx.get("Flags", 0)) & 0x00020000
    ):
        raise PaymentUnknown(
            "Validated transaction does not match the authorized exact payment."
        )
    return {
        "success": True,
        "transaction": attempt["tx_hash"],
        "network": "xrpl:1",
        "payer": tx.get("Account"),
        "ledger_index": result.get("ledger_index"),
        "network_fee_drops": str(tx.get("Fee", "0")),
        "validated": True,
    }


class MerchantPayments:
    def __init__(self, settings, store, name, fee):
        self.s, self.store, self.name, self.fee = settings, store, name, fee
        self.lock = asyncio.Lock()
        self.facilitator = None
        if settings.mode == "xrpl_testnet":
            self.facilitator = AsyncFacilitatorClient(
                FacilitatorClientOptions(settings.facilitator_url, timeout_seconds=40)
            )

    async def close(self):
        if self.facilitator:
            await self.facilitator.aclose()

    async def run(self, request, owner, body, perform, quantity=1, fee_multiplier=1):
        if not 1 <= quantity <= self.s.max_rfqs:
            raise HTTPException(422, "Invalid paid evaluation count.")
        if fee_multiplier not in {1, 2}:
            raise HTTPException(422, "Invalid service tariff.")
        unit_fee = self.fee * fee_multiplier
        fee = unit_fee * quantity
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 160:
            raise HTTPException(400, "A bounded Idempotency-Key is required.")
        operation_id = digest([self.name, owner, key])
        fingerprint = digest(
            [
                request.method,
                request.url.path,
                owner,
                body,
                fee,
                self.s.mode,
                self.s.pay_to,
            ]
        )
        async with self.lock:
            row = self.store.get("invoice", operation_id, owner)
            if row and row["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Idempotency-Key was already used for different input."
                )
            if not row:
                extra = {
                    "invoiceId": operation_id,
                    "bodyHash": digest(body),
                    "quantity": quantity,
                    "unitPrice": str(unit_fee),
                }
                if self.s.mode == "xrpl_testnet" and self.s.source_tag is not None:
                    extra["sourceTag"] = self.s.source_tag
                req = PaymentRequirements(
                    scheme="exact",
                    network="xrpl:1",
                    amount=str(fee),
                    asset="XRP",
                    pay_to=self.s.pay_to
                    if self.s.mode == "xrpl_testnet"
                    else "SIMULATED_MERCHANT",
                    max_timeout_seconds=180,
                    extra=extra,
                )
                row = {
                    "id": operation_id,
                    "owner": owner,
                    "fingerprint": fingerprint,
                    "body": body,
                    "requirements": req.to_dict(),
                    "mode": self.s.mode,
                    "state": "UNPAID",
                    "expires_at": time.time() + 900,
                    "created_at": time.time(),
                }
                self.store.put("invoice", operation_id, owner, row)
            if row.get("result") is not None:
                return JSONResponse(
                    row["result"], headers={"PAYMENT-RESPONSE": encode(row["receipt"])}
                )
            req = PaymentRequirements.from_dict(row["requirements"])
            header = request.headers.get("PAYMENT-SIGNATURE")
            if not header and row["state"] == "UNPAID":
                resource = ResourceInfo(
                    str(request.url),
                    f"{self.name}; {quantity} evaluation(s), one aggregate payment",
                    "application/json",
                )
                challenge = PaymentRequired(2, resource, [req]).to_dict()
                if self.s.mode == "mock":
                    challenge["simulation"] = True
                return JSONResponse(
                    challenge,
                    status_code=402,
                    headers={"PAYMENT-REQUIRED": encode(challenge)},
                )
            if row["state"] != "SETTLED":
                if not header:
                    raise HTTPException(
                        409,
                        "Resume with the original payment evidence; do not create another payment.",
                    )
                if len(header) > 50000:
                    raise HTTPException(400, "Payment header is too large.")
                try:
                    payload = decode(header)
                    if (
                        payload.get("x402Version") != 2
                        or payload.get("accepted") != req.to_dict()
                        or payload.get("payload", {}).get("invoiceId") != operation_id
                    ):
                        raise ValueError()
                except Exception:
                    raise HTTPException(
                        400, "Payment evidence does not match this invoice."
                    ) from None
                if row.get("proof_hash") and row["proof_hash"] != digest(payload):
                    raise HTTPException(
                        409, "Only the original signed payment may be resumed."
                    )
                if row["state"] == "UNPAID" and time.time() > row["expires_at"]:
                    raise HTTPException(410, "Invoice expired before payment.")
                row["proof_hash"] = digest(payload)
                if self.s.mode == "mock":
                    proof = payload.get("payload", {}).get("mockProof", "")
                    if not hmac.compare_digest(
                        proof, mock_proof(self.s.mock_secret, req.to_dict())
                    ):
                        raise HTTPException(402, "Invalid simulated payment proof.")
                    receipt = {
                        "success": True,
                        "simulation": True,
                        "network": "demo-credits",
                        "transaction": "",
                        "network_fee_drops": "0",
                        "payer": "SIVAX demo credits",
                    }
                else:
                    if "mockProof" in payload.get("payload", {}):
                        raise HTTPException(
                            402, "Simulated evidence is never accepted in Testnet mode."
                        )
                    blob = payload.get("payload", {}).get("signedTxBlob", "")
                    try:
                        row["attempt"] = {
                            "signed_tx_blob": blob,
                            "tx_hash": tx_hash(blob),
                        }
                    except ValueError:
                        raise HTTPException(
                            400, "Invalid signed transaction."
                        ) from None
                    receipt = None
                    if row["state"] in {"IN_FLIGHT", "UNKNOWN"}:
                        try:
                            receipt = await ledger_receipt(
                                self.s, row["attempt"], fee, self.s.pay_to
                            )
                        except Exception:
                            receipt = None
                    if not receipt:
                        try:
                            if row["state"] == "UNPAID":
                                verification = await self.facilitator.verify(
                                    payment_header=header, payment_requirements=req
                                )
                                if not verification.is_valid:
                                    row["verification_error"] = (
                                        verification.invalid_reason or "invalid_payment"
                                    )
                                    self.store.put("invoice", operation_id, owner, row)
                                    raise HTTPException(
                                        402,
                                        "Facilitator rejected payment: "
                                        + row["verification_error"],
                                    )
                            row["state"] = "IN_FLIGHT"
                            self.store.put("invoice", operation_id, owner, row)
                            settlement = await self.facilitator.settle(
                                payment_header=header, payment_requirements=req
                            )
                            if (
                                not settlement.success
                                or settlement.network != "xrpl:1"
                                or settlement.transaction.upper()
                                != row["attempt"]["tx_hash"]
                            ):
                                raise PaymentUnknown()
                            # Independently verify positive ledger evidence, not merely HTTP 200.
                            receipt = await ledger_receipt(
                                self.s, row["attempt"], fee, self.s.pay_to
                            )
                            if receipt is None:
                                raise PaymentUnknown()
                        except HTTPException:
                            raise
                        except Exception:
                            row["state"] = "UNKNOWN"
                            self.store.put("invoice", operation_id, owner, row)
                            raise HTTPException(
                                503,
                                "Payment outcome is unknown. Resume the original operation; its budget remains reserved.",
                            ) from None
                row["receipt"] = {
                    **receipt,
                    "invoice_id": operation_id,
                    "merchant": self.name,
                    "merchant_fee_drops": str(fee),
                    "quantity": quantity,
                    "unit_fee_drops": str(unit_fee),
                    "payment_mode": self.s.mode,
                    "payee": req.pay_to,
                    "state": "SETTLED",
                }
                row["state"] = "SETTLED"
                self.store.put("invoice", operation_id, owner, row)
            # Receipt is durable before business execution. The handler itself is idempotent.
            try:
                result = await perform(operation_id)
            except Exception:
                row["refund"] = {
                    "status": "PENDING_REVIEW",
                    "amount_drops": str(fee),
                    "reason": "Paid service result could not be delivered.",
                }
                self.store.put("invoice", operation_id, owner, row)
                raise HTTPException(
                    503,
                    "Fee settled; result recovery or refund review is pending. Retry this operation without repaying.",
                ) from None
            row["result"] = {**result, "payment_receipt": row["receipt"]}
            row.pop("refund", None)
            self.store.put("invoice", operation_id, owner, row)
            return JSONResponse(
                row["result"], headers={"PAYMENT-RESPONSE": encode(row["receipt"])}
            )


class Payer:
    def __init__(self, settings, store):
        self.s, self.store = settings, store
        self.lock = asyncio.Lock()

    def validate_requirements(self, req, fee, body):
        payee = self.s.pay_to if self.s.mode == "xrpl_testnet" else "SIMULATED_MERCHANT"
        is_discovery = "preferences" in body and "quantity" in body
        quantity = body["quantity"] if is_discovery else 1
        unit_fee = (
            self.s.rfq_fee * tier_multiplier(body.get("service_tier", "standard"))
            if is_discovery
            else self.s.order_fee
        )
        if (
            req.network != "xrpl:1"
            or req.scheme != "exact"
            or req.asset != "XRP"
            or req.amount != str(fee)
            or req.pay_to != payee
            or (req.extra or {}).get("bodyHash") != digest(body)
            or (req.extra or {}).get("quantity") != quantity
            or (req.extra or {}).get("unitPrice") != str(unit_fee)
        ):
            raise ValueError("Merchant payment requirements exceed the authorization.")
        if (
            self.s.mode == "xrpl_testnet"
            and (req.extra or {}).get("sourceTag") != self.s.source_tag
        ):
            raise ValueError(
                "Merchant SourceTag does not match the configured deployment."
            )

    async def pay(self, url, owner, key, body, fee):
        if url not in {
            self.s.orders_url + "/v1/catalog/search",
            self.s.orders_url + "/v1/orders",
        }:
            raise ValueError("Unapproved payment resource.")
        aid = digest([url, owner, key])
        async with self.lock:
            attempts = self.store.list("attempt")
            if any(
                a["id"] != aid and a["state"] in {"IN_FLIGHT", "UNKNOWN"}
                for a in attempts
            ):
                raise PaymentUnknown(
                    "A previous payment is unresolved. Recover it before using this wallet."
                )
            a = self.store.get("attempt", aid, owner)
            if a and a["fingerprint"] != digest(body):
                raise ValueError("Payment input changed under the same operation ID.")
            if a and a.get("result"):
                return a["result"]
            headers = {
                "Authorization": "Bearer " + self.s.service_key,
                "X-Session-ID": owner,
                "Idempotency-Key": key,
            }
            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                if not a:
                    response = await client.post(url, headers=headers, json=body)
                    if response.status_code == 200:
                        # Lost local state must not cause a new charge when merchant has a result.
                        result = response.json()
                        self.store.put(
                            "attempt",
                            aid,
                            owner,
                            {
                                "id": aid,
                                "fingerprint": digest(body),
                                "state": "SETTLED",
                                "result": result,
                            },
                        )
                        return result
                    if response.status_code != 402:
                        response.raise_for_status()
                    challenge = decode(response.headers["PAYMENT-REQUIRED"])
                    req = PaymentRequirements.from_dict(challenge["accepts"][0])
                    self.validate_requirements(req, fee, body)
                    if challenge.get("extensions"):
                        raise ValueError(
                            "This merchant requires extensions not authorized by the demo payment profile."
                        )
                    if self.s.mode == "mock":
                        if challenge.get("simulation") is not True:
                            raise ValueError("Expected a simulated merchant.")
                        header = encode(
                            {
                                "x402Version": 2,
                                "accepted": req.to_dict(),
                                "payload": {
                                    "invoiceId": req.invoice_id(),
                                    "mockProof": mock_proof(
                                        self.s.mock_secret, req.to_dict()
                                    ),
                                },
                            }
                        )
                        extra = {}
                    else:
                        if challenge.get("simulation"):
                            raise ValueError(
                                "Testnet mode refuses simulated merchants."
                            )
                        from x402_xrpl.client.presigned_payment_payer import (
                            XRPLPresignedPaymentPayer,
                            XRPLPresignedPaymentPayerOptions,
                        )
                        from xrpl.core.binarycodec import decode as decode_tx
                        from xrpl.wallet import Wallet

                        signer = XRPLPresignedPaymentPayer(
                            XRPLPresignedPaymentPayerOptions(
                                Wallet.from_seed(self.s.seed),
                                "xrpl:1",
                                self.s.rpc_url,
                                max_fee_drops=self.s.network_fee_cap,
                            )
                        )
                        signed = await asyncio.to_thread(signer.prepare_payment, req)
                        header = signed.payment_header
                        tx = decode_tx(signed.signed_tx_blob)
                        extra = {
                            "tx_hash": tx_hash(signed.signed_tx_blob),
                            "signed_tx_blob": signed.signed_tx_blob,
                            "last_ledger_sequence": tx.get("LastLedgerSequence"),
                            "network_fee_drops": str(tx["Fee"]),
                        }
                    a = {
                        "id": aid,
                        "owner": owner,
                        "fingerprint": digest(body),
                        "state": "IN_FLIGHT",
                        "payment_header": header,
                        "requirements": req.to_dict(),
                        **extra,
                    }
                    self.store.put("attempt", aid, owner, a)
                headers["PAYMENT-SIGNATURE"] = a["payment_header"]
                try:
                    response = await client.post(url, headers=headers, json=body)
                    response.raise_for_status()
                    result = response.json()
                    receipt = result["payment_receipt"]
                    if receipt["payment_mode"] != self.s.mode or receipt[
                        "merchant_fee_drops"
                    ] != str(fee):
                        raise ValueError("Unexpected receipt.")
                    a.update(state="SETTLED", result=result)
                    self.store.put("attempt", aid, owner, a)
                    return result
                except Exception:
                    a["state"] = "UNKNOWN"
                    self.store.put("attempt", aid, owner, a)
                    raise PaymentUnknown(
                        "Payment or delivery is unresolved. The signed attempt is saved; retrying will not sign a second payment."
                    ) from None
