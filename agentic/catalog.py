"""Saved demo quote outputs. No runtime pricing engine or upstream contract."""

import copy
import json
import secrets
import time
from decimal import ROUND_DOWN, Decimal
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException
from pydantic import AwareDatetime, Field, model_validator

from .domain import (
    ASSET_NAMES,
    CMA_VARIANTS,
    TERMS,
    Preferences,
    ServiceTier,
    StrictModel,
    quote_count,
)
from .market_calendar import CALENDAR, MARKET_TZ, maturity_date, start_date
from .store import digest

D = Decimal
DATA = Path(__file__).with_name("data") / "quotes.json"


class CatalogSearch(StrictModel):
    batch_id: str = Field(min_length=8, max_length=100)
    preferences: Preferences
    quantity: int = Field(ge=1, le=36, strict=True)
    service_tier: ServiceTier = "standard"
    as_of: AwareDatetime

    @model_validator(mode="after")
    def capacity(self):
        if self.quantity > quote_count(self.preferences):
            raise ValueError("Requested quantity exceeds this collection.")
        return self


@lru_cache(maxsize=1)
def fixtures():
    raw = DATA.read_bytes()
    data = json.loads(raw)
    if (
        data.get("schema") != "sivax.quote-fixtures.v1"
        or data.get("simulated") is not True
        or data.get("unit_notional") != "1"
    ):
        raise RuntimeError("Invalid saved quote catalog.")
    if set(data["supported_terms"]) != set(TERMS) or set(
        data["supported_currencies"]
    ) != {"XRP", "RLUSD"}:
        raise RuntimeError("Incomplete saved quote catalog.")
    ids = set()
    for r in data["records"]:
        if (
            r["id"] in ids
            or not D(r["annualized_coupon"]).is_finite()
            or not D(0) <= D(r["annualized_coupon"]) <= D(1)
        ):
            raise RuntimeError("Invalid or duplicate saved quote.")
        ids.add(r["id"])
        if r["product_type"] == "cma":
            valid = (
                not r["underlyings"]
                and r["strike_percent"] is None
                and r["cma_variant"] in CMA_VARIANTS
                and all(D(x["principal_redemption_ratio"]) == 1 for x in r["scenarios"])
            )
        else:
            valid = (
                r["product_type"] == "fcn"
                and len(set(r["underlyings"])) == 2
                and set(r["underlyings"]) <= set(ASSET_NAMES)
                and r["strike_percent"] in {60, 70, 80, 90}
                and r["cma_variant"] is None
            )
        if not valid:
            raise RuntimeError(
                "Saved product violates its protection or asset constraint."
            )
    return data["records"], sha256(raw).hexdigest()


def eligible(p):
    records, _ = fixtures()
    if p.principal_protected:
        variants = list(CMA_VARIANTS)
        offset = (p.pool_round * 12) % len(variants)
        chosen = set((variants + variants)[offset : offset + 12])
        return [
            r
            for r in records
            if r["product_type"] == "cma" and r["cma_variant"] in chosen
        ]
    return [
        r
        for r in records
        if r["product_type"] == "fcn" and set(r["underlyings"]).issubset(p.asset_pool)
    ]


def project(record, preferences, as_of):
    """Attach current dates and scale saved unit yields to the exact notional."""
    p = preferences
    beginning = start_date(as_of)
    end = maturity_date(beginning, p.investment_term)
    days = (end - beginning).days
    annual = D(record["annualized_coupon"])
    period = annual * D(days) / 365
    protected = record["product_type"] == "cma"
    terms = {
        "underlyings": list(record["underlyings"]),
        "product_type": record["product_type"],
        "cma_variant": record["cma_variant"],
        "principal_protected": protected,
        "amount": p.amount,
        "currency": p.currency,
        "strike_percent": record["strike_percent"],
        "knock_in": False,
        "autocall": False,
        "monthly_coupon": False,
        "maturity": p.investment_term,
        "valuation_date": as_of.astimezone(MARKET_TZ).date().isoformat(),
        "pricing_at": as_of.isoformat(),
        "start_date": beginning.isoformat(),
        "maturity_date": end.isoformat(),
        "business_day_calendar": CALENDAR,
        "market_timezone": str(MARKET_TZ),
        "day_count": "ACT/365F",
        "days": days,
        "coupon_schedule": "At maturity",
        "payoff_version": "saved-demo-payoff-v1",
        "settlement_currency": p.currency,
    }
    return {
        "terms": terms,
        "terms_hash": digest(terms),
        "product_id": digest([record["id"], p.investment_term, p.currency])[:24],
        "display_name": "SIVAX CMA · " + CMA_VARIANTS[record["cma_variant"]]
        if protected
        else " + ".join(record["underlyings"]),
        "underlying_names": [ASSET_NAMES[a] for a in record["underlyings"]],
        "result": {
            "annualized_coupon": record["annualized_coupon"],
            "coupon_per_period": str(
                period.quantize(D("0.000001"), rounding=ROUND_DOWN)
            ),
            "coupon_amount": str(
                (D(p.amount) * period).quantize(D("0.000001"), rounding=ROUND_DOWN)
            ),
            "currency": p.currency,
        },
        "scenarios": copy.deepcopy(record["scenarios"]),
        "min_amount": "1",
        "max_amount": "100000",
        "size_increment": "0.000001",
        "capacity": "100000",
        "seller_id": "SIVAX",
        "simulated": True,
        "quote_source": "precomputed_catalog",
        "fixture_id": record["id"],
        "protection_disclosure": "Principal protection exists only in this simulated payoff. No actual guarantee, issuer or investment is provided."
        if protected
        else "Principal can be lost. Strike is not a maximum loss limit.",
    }


class QuoteBook:
    def __init__(self, settings, db):
        self.s, self.db = settings, db
        fixtures()  # Fail at startup if the shipped dataset is missing/corrupt.

    def prepare_batch(self, owner, key, request):
        fingerprint = digest(request.model_dump(mode="json"))
        with self.db.tx() as c:
            existing = self.db.get("catalog_batch", key, owner, c)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This batch already belongs to a different selection."
                    )
                return existing
            candidates = eligible(request.preferences)
            if len(candidates) < request.quantity:
                raise HTTPException(
                    422, "No complete matching saved collection is available."
                )
            # Draw once, then save every projected quote before the x402 challenge.
            selected = secrets.SystemRandom().sample(candidates, request.quantity)
            created = time.time()
            quotes = []
            for record in selected:
                quote_id = digest([key, record["id"]])
                quotes.append(
                    {
                        **project(record, request.preferences, request.as_of),
                        "quote_id": quote_id,
                        "rfq_id": quote_id,
                        "batch_id": request.batch_id,
                        "created_at": created,
                        "expires_at": created + self.s.quote_ttl,
                    }
                )
            row = {
                "fingerprint": fingerprint,
                "catalog_hash": fixtures()[1],
                "quotes": quotes,
                "created_at": created,
            }
            self.db.put("catalog_batch", key, owner, row, c)
            return row

    def publish(self, owner, quotes):
        with self.db.tx() as c:
            for q in quotes:
                self.db.put("quote", q["quote_id"], owner, q, c)

    def quote(self, owner, key):
        q = self.db.get("quote", key, owner)
        if not q:
            raise HTTPException(404, "Quote not found.")
        return q

    def reserve(self, owner, quote_id, search_id):
        rid = digest([owner, quote_id, search_id])
        with self.db.tx() as c:
            old = self.db.get("reservation", rid, owner, c)
            if old:
                return old
            q = self.db.get("quote", quote_id, owner, c)
            if not q or q["expires_at"] <= time.time():
                raise HTTPException(
                    410, "Quote expired. A new search requires authorization."
                )
            for previous in self.db.list("reservation", owner, c):
                if previous["state"] == "HELD":
                    previous["state"] = "RELEASED"
                    self.db.put("reservation", previous["id"], owner, previous, c)
            result = {
                "id": rid,
                "quote_id": quote_id,
                "terms_hash": q["terms_hash"],
                "state": "HELD",
                "expires_at": time.time() + self.s.hold_ttl,
                "quote": q,
            }
            self.db.put("reservation", rid, owner, result, c)
            return result

    def reservation(self, owner, key):
        row = self.db.get("reservation", key, owner)
        if not row:
            raise HTTPException(404, "Reservation not found.")
        return row

    def lease(self, owner, key, body):
        with self.db.tx() as c:
            r = self.db.get("reservation", key, owner, c)
            if not r:
                raise HTTPException(404, "Reservation not found.")
            if r.get("intent_id") == body.intent_id:
                if r["amount"] != body.amount or r["currency"] != body.currency:
                    raise HTTPException(
                        409, "Intent amount and currency cannot change."
                    )
                return r
            if r["state"] != "HELD" or r["expires_at"] <= time.time():
                raise HTTPException(410, "Reservation is no longer available.")
            if (
                body.amount != r["quote"]["terms"]["amount"]
                or body.currency != r["quote"]["terms"]["currency"]
            ):
                raise HTTPException(
                    409, "Execution amount and currency must match the quote."
                )
            r.update(
                state="LEASED",
                intent_id=body.intent_id,
                amount=body.amount,
                currency=body.currency,
                lease_expires_at=time.time() + self.s.execution_ttl,
            )
            self.db.put("reservation", key, owner, r, c)
            return r

    def consume(self, owner, key, body):
        with self.db.tx() as c:
            r = self.db.get("reservation", key, owner, c)
            if (
                not r
                or r.get("intent_id") != body.intent_id
                or r.get("amount") != body.amount
                or r.get("currency") != body.currency
            ):
                raise HTTPException(
                    409, "Execution does not match the reserved intent."
                )
            if r["state"] == "CONSUMED":
                return r
            if r["state"] != "LEASED" or r["lease_expires_at"] <= time.time():
                raise HTTPException(
                    410, "Execution lease expired; fee recovery review may be required."
                )
            r["state"] = "CONSUMED"
            self.db.put("reservation", key, owner, r, c)
            return r
