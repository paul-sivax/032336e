from uuid import uuid4

import httpx
import pytest
from test_batch import batch_body

from agentic import catalog
from agentic.catalog import CatalogSearch, QuoteBook
from agentic.config import Settings
from agentic.core import Core
from agentic.domain import AgentRequest
from agentic.merchant import create_app
from agentic.store import Store


def test_random_draw_happens_once_and_survives_reconstruction(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    db = Store(tmp_path / "catalog.sqlite3")
    request = CatalogSearch.model_validate(batch_body(30))
    book = QuoteBook(settings, db)
    draws = []

    class Draw:
        def sample(self, population, size):
            draws.append(size)
            return population[:size] if len(draws) == 1 else population[-size:]

    monkeypatch.setattr(catalog.secrets, "SystemRandom", Draw)
    first = book.prepare_batch("owner", "first", request)
    second = book.prepare_batch("owner", "second", request)
    assert {q["fixture_id"] for q in first["quotes"]}.isdisjoint(
        {q["fixture_id"] for q in second["quotes"]}
    )
    monkeypatch.setattr(catalog, "eligible", lambda p: pytest.fail("A retry rerolled"))
    reconstructed = QuoteBook(settings, Store(tmp_path / "catalog.sqlite3"))
    assert reconstructed.prepare_batch("owner", "first", request) == first
    assert draws == [30, 30]


async def test_missing_collection_fails_before_invoice_and_no_pricing_docs(
    tmp_path, monkeypatch
):
    app = create_app(Settings(data_dir=tmp_path))
    headers = {
        "Authorization": "Bearer local-service-only",
        "X-Session-ID": "owner",
        "Idempotency-Key": uuid4().hex,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://merchant"
    ) as client:
        for route in ("/openapi.json", "/docs", "/redoc"):
            assert (await client.get(route)).status_code == 404
        for route in ("/v1/rfqs", "/v1/rfqs/batch"):
            assert (
                await client.post(route, headers=headers, json={})
            ).status_code == 404
        monkeypatch.setattr(catalog, "eligible", lambda p: [])
        response = await client.post(
            "/v1/catalog/search", headers=headers, json=batch_body()
        )
        assert response.status_code == 422
        assert app.state.db.list("invoice") == []
        assert app.state.db.list("catalog_batch") == []


def test_corrupt_shipped_catalog_fails_startup(tmp_path, monkeypatch):
    invalid = tmp_path / "quotes.json"
    invalid.write_text('{"schema":"wrong"}')
    catalog.fixtures.cache_clear()
    try:
        monkeypatch.setattr(catalog, "DATA", invalid)
        with pytest.raises(RuntimeError, match="Invalid saved"):
            create_app(Settings(data_dir=tmp_path))
    finally:
        catalog.fixtures.cache_clear()


async def test_pre_catalog_saved_search_cannot_create_another_payment(
    tmp_path, monkeypatch
):
    db = Store(tmp_path / "core.sqlite3")
    core = Core(Settings(data_dir=tmp_path), db)
    job = core.start(
        "owner",
        "browser",
        AgentRequest(
            operation="find_fcn_quotes",
            operation_id=uuid4().hex,
            preferences=batch_body()["preferences"],
        ),
    )
    job.pop("quote_source")
    job["state"] = "queued"
    core.save(job)

    async def forbid(*args, **kwargs):
        pytest.fail("Legacy recovery created a new catalog payment")

    monkeypatch.setattr(core.payer, "pay", forbid)
    await core.run(job["id"])
    assert core.get("owner", job["id"])["state"] == "failed"
    assert "original application" in core.get("owner", job["id"])["error"]
    assert db.list("attempt") == []
