from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Term = Literal["1bd", "2bd", "3bd", "5bd", "10bd", "7d", "1m", "3m", "6m", "1y"]
TERMS = ("1bd", "2bd", "3bd", "5bd", "10bd", "7d", "1m", "3m", "6m", "1y")
ASSET_NAMES = {
    "TSLA": "Tesla",
    "NVDA": "NVIDIA",
    "AMZN": "Amazon",
    "XRPN": "Evernorth",
    "MA": "Mastercard",
    "PLTR": "Palantir",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "ORCL": "Oracle",
    "NFLX": "Netflix",
    "CRM": "Salesforce",
    "UBER": "Uber",
    "SHOP": "Shopify",
    "SNOW": "Snowflake",
    "LLY": "Eli Lilly",
    "COST": "Costco",
    "WMT": "Walmart",
    "GE": "GE Aerospace",
    "CAT": "Caterpillar",
    "JPM": "JPMorgan Chase",
}
ASSETS = tuple(ASSET_NAMES)
DEFAULT_POOL = list(ASSETS[:6])
CMA_VARIANTS = {
    f"cash-{i:02d}": f"{theme} {style}"
    for i, (theme, style) in enumerate(
        (
            (theme, style)
            for theme in (
                "Everyday",
                "Reserve",
                "Treasury",
                "Flexible",
                "Steady",
                "Prime",
            )
            for style in ("Cash", "Balance", "Saver", "Plus", "Select", "Reserve")
        ),
        1,
    )
}
SELLER = "SIVAX"
Currency = Literal["XRP", "RLUSD"]
ServiceTier = Literal["standard", "turbo"]


def tier_multiplier(tier):
    if tier not in {"standard", "turbo"}:
        raise ValueError("Unsupported service tier.")
    return 2 if tier == "turbo" else 1


D = Decimal


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Investment(StrictModel):
    amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?$")
    currency: Currency

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        n = D(value)
        if not D(1) <= n <= D(100000):
            raise ValueError(
                "Choose an amount from 1 to 100,000, with up to 6 decimals."
            )
        return format(n.normalize(), "f")


class Preferences(Investment):
    investment_term: Term
    principal_protected: bool = Field(strict=True)
    asset_pool: list[str] = Field(
        default_factory=lambda: DEFAULT_POOL.copy(), min_length=2, max_length=6
    )
    pool_round: int = Field(default=0, ge=0, le=9999)

    @field_validator("asset_pool")
    @classmethod
    def allowed_assets(cls, values):
        if len(set(values)) != len(values) or any(v not in ASSETS for v in values):
            raise ValueError("Choose 2 to 6 distinct assets from the SIVAX collection.")
        return [v for v in ASSETS if v in values]


class AgentRequest(StrictModel):
    schema_version: Literal["sivax.agent-request.v4"] = "sivax.agent-request.v4"
    operation: Literal["find_fcn_quotes", "prepare_demo_order", "execute_demo_order"]
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    preferences: Preferences | None = None
    spend_authorization_id: str | None = None
    search_id: str | None = None
    amount: str | None = None
    currency: Currency | None = None
    intent_id: str | None = None
    quote_id: str | None = None
    service_tier: ServiceTier = "standard"

    @model_validator(mode="after")
    def validate_operation(self):
        if self.operation == "prepare_demo_order" and not (
            self.search_id and self.amount and self.currency
        ):
            raise ValueError(
                "Preparing an order requires search_id, amount and currency."
            )
        if self.amount is not None:
            if self.currency is None:
                raise ValueError("Currency is required with an amount.")
            self.amount = Investment(amount=self.amount, currency=self.currency).amount
        if self.operation == "execute_demo_order" and not self.intent_id:
            raise ValueError("Executing an order requires intent_id.")
        return self


def quote_count(p: Preferences, limit=36):
    # Keep the authorized batch size consistent across Standard and Turbo.
    return min(
        limit,
        12
        if p.principal_protected
        else min(30, len(p.asset_pool) * (len(p.asset_pool) - 1)),
    )


def next_pool(p: Preferences):
    data = p.model_dump(mode="json")
    data["pool_round"] = (p.pool_round + 1) % 10000
    offset = (data["pool_round"] * 6) % len(ASSETS)
    data["asset_pool"] = list(ASSETS[offset : offset + 6])
    return Preferences.model_validate(data)


def rank(quotes, p: Preferences):
    matching = [
        q
        for q in quotes
        if q["terms"]["maturity"] == p.investment_term
        and q["terms"]["amount"] == p.amount
        and q["terms"]["currency"] == p.currency
        and q["terms"]["principal_protected"] == p.principal_protected
        and set(q["terms"]["underlyings"]).issubset(p.asset_pool)
    ]
    for q in matching:
        q["ranking"] = {
            "annual_coupon": q["result"]["annualized_coupon"],
            "stress_principal_loss": str(
                1 - D(q["scenarios"][2]["principal_redemption_ratio"])
            ),
            "explanation": "Your best match from the SIVAX collection: the highest coupon among evaluated products matching your exact term, amount, currency and principal protection choice.",
        }
    return sorted(
        matching,
        key=lambda q: (
            -D(q["result"]["annualized_coupon"]),
            q["terms"]["strike_percent"] or 0,
            q["quote_id"],
        ),
    )
