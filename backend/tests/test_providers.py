import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers import JsonApiProvider, ProviderConfig, merge_products
from app.schemas import Product


def provider_product(raw, store):
    raw = deepcopy(raw)
    raw["offers"] = [offer for offer in raw["offers"] if offer["store"] == store]
    raw["source"] = "live"
    for offer in raw["offers"]:
        offer["source"] = "live"
    return Product.model_validate(raw)


async def test_transient_failure_is_retried(sample_catalog):
    calls = []
    product = provider_product(sample_catalog[0], "daraz")

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            503 if len(calls) == 1 else 200,
            json={"products": [product.model_dump(mode="json", by_alias=True)]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = JsonApiProvider(
            ProviderConfig(store="daraz", endpoint="https://approved.example/products"), client
        )
        result = await provider.fetch("headphones")
    assert len(calls) == 2 and result[0].source == "live"


async def test_permanent_failure_is_not_retried():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"detail": "Rejected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = JsonApiProvider(
            ProviderConfig(store="daraz", endpoint="https://approved.example/products"), client
        )
        with pytest.raises(httpx.HTTPStatusError):
            await provider.fetch("headphones")
    assert len(calls) == 1


async def test_offer_links_cannot_point_to_unrelated_domains(sample_catalog):
    raw = provider_product(sample_catalog[0], "daraz").model_dump(mode="json", by_alias=True)
    raw["offers"][0]["url"] = "https://untrusted.example/product"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"products": [raw]}))
    ) as client:
        provider = JsonApiProvider(
            ProviderConfig(store="daraz", endpoint="https://approved.example/products"), client
        )
        with pytest.raises(ValueError, match="configured marketplace"):
            await provider.fetch("headphones")


def test_canonical_merge_preserves_store_offers_and_rejects_id_conflicts(sample_catalog):
    daraz = provider_product(sample_catalog[0], "daraz")
    amazon = provider_product(sample_catalog[0], "amazon")
    merged = merge_products([[daraz], [amazon]])
    assert len(merged) == 1 and len(merged[0].offers) == 2
    amazon.name = "A completely different model"
    with pytest.raises(ValueError, match="conflicting"):
        merge_products([[daraz], [amazon]])


class FakeProvider:
    def __init__(self, store, product=None, error=False):
        self.config = ProviderConfig(store=store, endpoint="https://approved.example/products")
        self.product, self.error, self.calls = product, error, 0

    async def fetch(self, _):
        self.calls += 1
        if self.error:
            raise RuntimeError("Upstream unavailable")
        return [self.product.model_copy(deep=True)] if self.product else []


def test_partial_outage_returns_healthy_live_results_and_retries_failure(settings, sample_catalog):
    healthy = FakeProvider("daraz", provider_product(sample_catalog[0], "daraz"))
    broken = FakeProvider("amazon", error=True)
    with TestClient(
        create_app(settings.model_copy(update={"catalog_mode": "live"}), [healthy, broken])
    ) as client:
        for _ in range(2):
            response = client.get("/api/products", params={"q": "headphones"})
            assert response.status_code == 200
            data = response.json()
            assert data["partial"] is True and data["mode"] == "live"
            assert len(data["products"]) == 1 and data["products"][0]["source"] == "live"
            assert client.get("/api/products/sony-xm5/offers").json()["source"] == "live"
        assert broken.calls == 2


def test_live_mode_has_no_silent_demo_fallback(settings):
    with TestClient(create_app(settings.model_copy(update={"catalog_mode": "live"}), [])) as client:
        response = client.get("/api/products", params={"q": "headphones"})
        assert response.status_code == 503
        assert "no enabled provider" in response.json()["detail"]


def test_all_provider_failures_return_service_unavailable(settings):
    with TestClient(
        create_app(settings.model_copy(update={"catalog_mode": "live"}), [FakeProvider("daraz", error=True)])
    ) as client:
        assert client.get("/api/products", params={"q": "headphones"}).status_code == 503
        assert client.get("/api/providers").json()["providers"][0]["status"] == "error"


async def test_providers_are_fetched_concurrently(settings, sample_catalog):
    from app.database import create_database, initialize_database
    from app.providers import CatalogService

    started = set()
    both_started = asyncio.Event()

    class BarrierProvider(FakeProvider):
        async def fetch(self, query):
            started.add(self.config.store)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return await super().fetch(query)

    live = settings.model_copy(update={"catalog_mode": "live"})
    engine, sessions = create_database(live)
    await initialize_database(engine, sessions, live)
    try:
        async with httpx.AsyncClient() as client:
            service = CatalogService(
                live,
                sessions,
                client,
                [
                    BarrierProvider("daraz", provider_product(sample_catalog[0], "daraz")),
                    BarrierProvider("amazon", provider_product(sample_catalog[0], "amazon")),
                ],
            )
            products, statuses, hit = await service.catalog("headphones")
        assert len(products) == 1 and len(products[0].offers) == 2
        assert all(status["status"] == "ok" for status in statuses) and hit is False
    finally:
        await engine.dispose()
