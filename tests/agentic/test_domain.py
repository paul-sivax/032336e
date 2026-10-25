from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agentic.catalog import CatalogSearch, eligible, fixtures, project
from agentic.config import Settings
from agentic.domain import ASSETS, Preferences, quote_count, rank
from agentic.market_calendar import maturity_date, pricing_time

PREF = dict(
    investment_term="1bd", amount="100", currency="XRP", principal_protected=False
)


def test_business_day_and_calendar_month_maturities():
    assert maturity_date(date(2026, 10, 23), "1bd") == date(2026, 10, 26)
    assert maturity_date(date(2026, 10, 24), "1bd") == date(2026, 10, 27)
    assert maturity_date(date(2026, 10, 23), "2bd") == date(2026, 10, 27)
    assert maturity_date(date(2026, 10, 23), "5bd") == date(2026, 10, 30)
    assert maturity_date(date(2026, 1, 31), "1m") == date(2026, 3, 2)
    assert maturity_date(date(2028, 2, 29), "1y") == date(2029, 2, 28)


def test_saved_catalog_coverage_and_exact_preferences():
    assert len(fixtures()[0]) == 1140
    assert len(ASSETS) == 24
    assert {"TSLA", "NVDA", "AMZN", "XRPN", "MA"} <= set(ASSETS)
    assert not {"BTC", "ETH", "V"} & set(ASSETS)
    p = Preferences(**PREF)
    candidates = eligible(p)
    assert len(candidates) == 60 and quote_count(p) == 30
    quotes = [
        dict(project(r, p, pricing_time(date(2026, 10, 23))), quote_id=r["id"])
        for r in candidates[:30]
    ]
    assert quotes[0]["terms"]["days"] == 3
    assert all(
        q["terms"]["maturity"] == "1bd"
        and q["terms"]["amount"] == "100"
        and not q["terms"]["principal_protected"]
        for q in quotes
    )
    protected = Preferences(**{**PREF, "principal_protected": True})
    protected_quotes = [
        dict(project(r, protected, pricing_time(date(2026, 10, 23))), quote_id=r["id"])
        for r in eligible(protected)
    ]
    assert len(protected_quotes) == quote_count(protected) == 12
    assert all(
        q["terms"]["product_type"] == "cma"
        and not q["terms"]["underlyings"]
        and q["terms"]["strike_percent"] is None
        for q in protected_quotes
    )
    assert all(
        Decimal(s["principal_redemption_ratio"]) == 1
        for q in protected_quotes
        for s in q["scenarios"]
    )
    ranked = rank(quotes + protected_quotes, p)
    assert len(ranked) == 30
    assert Decimal(ranked[0]["result"]["annualized_coupon"]) == max(
        Decimal(q["result"]["annualized_coupon"]) for q in quotes
    )
    assert len(rank(quotes + protected_quotes, protected)) == 12
    assert not rank(quotes, Preferences(**{**PREF, "amount": "200"}))
    assert not rank(quotes, Preferences(**{**PREF, "currency": "RLUSD"}))


def test_saved_unit_yield_scales_without_repricing():
    p = Preferences(
        **{
            **PREF,
            "investment_term": "1m",
            "asset_pool": ["NVDA", "TSLA"],
            "amount": "250.123456",
            "currency": "RLUSD",
        }
    )
    row = next(r for r in eligible(p) if r["strike_percent"] == 80)
    q = project(row, p, pricing_time(date(2026, 1, 31)))
    assert q["result"]["annualized_coupon"] == row["annualized_coupon"]
    assert q["scenarios"] == row["scenarios"]
    assert q["scenarios"][2]["principal_redemption_ratio"] == "0.625"
    expected = (
        Decimal(p.amount) * Decimal(row["annualized_coupon"]) * 28 / 365
    ).quantize(Decimal("0.000001"), rounding="ROUND_DOWN")
    assert Decimal(q["result"]["coupon_amount"]) == expected
    assert q == project(row, p, pricing_time(date(2026, 1, 31)))


def test_invalid_profiles_and_count_rejected_before_billing():
    for field, value in [
        ("amount", "0"),
        ("amount", "1.0000001"),
        ("principal_protected", "false"),
        ("investment_term", "max_3m"),
        ("currency", "USD"),
        ("currency", "xrp"),
        ("amount", "1e3"),
        ("amount", 100),
        ("asset_pool", ["BTC", "ETH"]),
        ("asset_pool", ["NVDA", "V"]),
        ("asset_pool", ["TSLA", "TSLA"]),
    ]:
        with pytest.raises(ValidationError):
            Preferences(**{**PREF, field: value})
    with pytest.raises(ValidationError):
        CatalogSearch(
            batch_id="invalid-count",
            preferences=PREF,
            quantity=31,
            as_of="2026-10-24T12:00:00Z",
        )
    with pytest.raises(ValidationError):
        CatalogSearch(
            batch_id="invalid-count",
            preferences={**PREF, "principal_protected": True},
            quantity=13,
            as_of="2026-10-24T12:00:00Z",
        )
    with pytest.raises(ValidationError):
        CatalogSearch(
            batch_id="invalid-time",
            preferences=PREF,
            quantity=3,
            as_of="2026-10-24T12:00:00",
        )


def test_mainnet_and_unimplemented_risk_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        Settings(network="xrpl:0", data_dir=tmp_path).validate()
    with pytest.raises(ValueError):
        Settings(risk_mode="t54_enforced", data_dir=tmp_path).validate()
    Settings(mode="mock", risk_mode="off", data_dir=tmp_path).validate()
