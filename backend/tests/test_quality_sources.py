import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import ShopifyStoreConfig
from app.main import create_app
from app.marketplaces import MarketplaceAccessError, PublicMarketplaceProvider, parse_page
from app.recommendations import rank_products
from app.schemas import Product
from app.shopify import ShopifyProvider, parse_product_page


def product(identifier, rating=4.7, sold=1000, price=998, store="daraz", host="www.daraz.pk"):
    return Product.model_validate(
        {
            "id": identifier,
            "name": "Wireless headphones",
            "category": "audio",
            "source": "live",
            "rating": rating,
            "reviews": 1000,
            "soldCount": sold,
            "offers": [
                {
                    "store": store,
                    "price": price,
                    "source": "live",
                    "rating": rating,
                    "reviews": 1000,
                    "soldCount": sold,
                    "url": f"https://{host}/products/headphones",
                }
            ],
        }
    )


class Source:
    def __init__(self, products, identity="daraz", name="Daraz"):
        self.products, self.calls = products, 0
        self.config = SimpleNamespace(
            store=products[0].offers[0].store, source_id=identity, display_name=name
        )

    async def fetch(self, query):
        self.calls += 1
        return self.products


def test_quality_filters_exclude_missing_metrics_and_include_exact_limits():
    listings = [
        product("boundary", 4, 100),
        product("low-rating", 3.9, 1000),
        product("low-sales", 5, 99),
        product("unrated", None, 1000),
        product("unknown-sales", 5, None),
    ]
    eligible = rank_products(listings, "headphones", min_rating=4, min_sales=100, sort="best-selling")
    assert [p.id for p in eligible] == ["boundary"]
    assert len(rank_products(listings, min_rating=0, min_sales=0)) == 5


def test_quality_filters_and_display_metrics_use_the_eligible_source_offer():
    listing = product("shared", 4.9, 10000)
    bad = listing.offers[0].model_copy(update={"rating": 2, "price": 10})
    good = bad.model_copy(
        update={"store": "amazon", "rating": 4.8, "reviews": 200, "sold_count": 1000, "price": 1000}
    )
    unknown = bad.model_copy(update={"rating": None, "sold_count": 50000, "price": 1})
    listing.offers = [bad, good, unknown]
    ranked = rank_products([listing], min_rating=4.5, min_sales=100)[0]
    assert len(ranked.offers) == 1 and ranked.offers[0].store == "amazon"
    assert (
        ranked.price == 1000 and ranked.rating == 4.8 and ranked.reviews == 200 and ranked.sold_count == 1000
    )


def test_combined_ranking_prioritizes_strong_sales_and_rating_over_cheap_price():
    cheap = product("cheap", 4.1, 100, 100)
    popular = product("popular", 4.8, 20000, 7000)
    ranked = rank_products([cheap, popular], "headphones", sort="best-selling", min_rating=4, min_sales=100)
    assert ranked[0].id == "popular" and ranked[0].badge == "Best sales & rating"
    assert ranked[0].recommendation["weights"]["sales"] == 0.3


def test_rest_quality_filters_validate_and_do_not_leak_between_cached_searches(settings):
    source = Source([product("headphones")])
    with TestClient(create_app(settings.model_copy(update={"catalog_mode": "live"}), [source])) as client:
        assert client.get("/api/products", params={"q": "headphones", "minRating": 4.8}).json()["total"] == 0
        response = client.get(
            "/api/products",
            params={"q": "headphones", "minRating": 4.5, "minSales": 100, "sort": "best-selling"},
        )
        assert response.status_code == 200 and response.json()["total"] == 1 and source.calls == 1
        for filters in ({"minRating": 6}, {"minSales": -1}, {"minSales": "1.1"}):
            assert client.get("/api/products", params={"q": "headphones", **filters}).status_code == 422


def test_websocket_uses_the_same_quality_filters(settings):
    source = Source([product("good"), product("low-rating", 2.5, 9000), product("low-sales", 5, 20)])
    with TestClient(create_app(settings.model_copy(update={"catalog_mode": "live"}), [source])) as client:
        with client.websocket_connect(
            "/api/ws/search", headers={"origin": "http://localhost:8001"}
        ) as socket:
            socket.send_json(
                {
                    "requestId": "quality",
                    "q": "headphones",
                    "minRating": 4,
                    "minSales": 100,
                    "sort": "best-selling",
                }
            )
            events = []
            while not events or events[-1]["type"] != "complete":
                events.append(socket.receive_json())
            assert [p["id"] for p in events[-1]["products"]] == ["good"]
            assert events[0]["products"] == []


def test_aliexpress_embedded_public_results_preserve_sales_rating_and_conditional_shipping():
    listing = {
        "productId": "1005006409533255",
        "title": {"displayTitle": "Wireless headphones"},
        "prices": {
            "salePrice": {"currencyCode": "PKR", "minPrice": 6896.1},
            "originalPrice": {"minPrice": 6899.21},
        },
        "evaluation": {"starRating": 4.6},
        "trade": {"tradeDesc": "278 sold"},
        "image": {"imgUrl": "//ae-pic-a1.aliexpress-media.com/photo.jpg"},
        "sellingPoints": [{"tagContent": {"tagText": "Free shipping over Rs.3,113"}}],
    }
    data = {"data": {"root": {"fields": {"items": [listing, listing]}}}}
    html = "<script>window._dida_config_._init_data_= { data: " + json.dumps(data) + " };</script>"
    results = parse_page(html.encode(), "aliexpress", 24)
    assert len(results) == 1
    result = results[0]
    assert (
        result.price == 6896.1
        and result.currency == "PKR"
        and result.rating == 4.6
        and result.sold_count == 278
    )
    assert result.reviews is None and result.offers[0].shipping_fee is None
    assert str(result.image_url).startswith("https://") and str(result.offers[0].url).endswith(
        "1005006409533255.html"
    )
    assert "Free shipping over" in result.overview[0]


def test_unsupported_aliexpress_script_does_not_become_a_fake_listing():
    with pytest.raises(MarketplaceAccessError):
        parse_page(b"<script>window.runParams = { unsupportedJavascript: true };</script>", "aliexpress", 24)


async def test_shopify_search_preserves_decimal_currency_and_original_store_link(monkeypatch):
    requests = []

    async def public_address(self):
        pass

    monkeypatch.setattr(ShopifyProvider, "_validate_public_address", public_address)

    def transport(request):
        requests.append(request)
        if request.url.path.endswith("search/suggest.json"):
            return httpx.Response(
                200,
                json={
                    "resources": {
                        "results": {
                            "products": [
                                {
                                    "id": 123,
                                    "title": "Wireless headphones",
                                    "price": "40.99",
                                    "available": True,
                                    "url": "/products/headphones",
                                    "vendor": "Audio brand",
                                }
                            ]
                        }
                    }
                },
            )
        if request.headers.get("Accept") != "text/html":
            return httpx.Response(200, json={"product": {"title": "Wireless headphones"}})
        return httpx.Response(
            200,
            text='<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "Product",
                    "name": "Wireless headphones",
                    "url": "/products/headphones",
                    "description": "<p>Clear stereo sound</p>",
                    "aggregateRating": {"ratingValue": 4.8, "reviewCount": 124},
                }
            )
            + "</script>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = ShopifyProvider(
            ShopifyStoreConfig(name="Audio store", url="https://audio.example.com/en/", currency="CAD"),
            client,
        )
        result = (await provider.fetch("headphones"))[0]
        assert (
            requests[0].url.path == "/en/search/suggest.json" and requests[0].url.params["q"] == "headphones"
        )
        assert (
            result.price == 40.99
            and result.currency == "CAD"
            and result.rating == 4.8
            and result.reviews == 124
        )
        assert result.overview == ["Clear stereo sound"] and result.sold_count is None
        assert (
            result.offers[0].store_name == "Audio store" and result.offers[0].url.host == "audio.example.com"
        )
        assert not rank_products([result], min_sales=100)


async def test_shopify_skips_foreign_product_urls_and_keeps_results_when_extra_details_fail(monkeypatch):
    async def public_address(self):
        pass

    monkeypatch.setattr(ShopifyProvider, "_validate_public_address", public_address)

    def transport(request):
        assert request.url.host == "audio.example.com"
        if request.url.path.endswith("search/suggest.json"):
            return httpx.Response(
                200,
                json={
                    "resources": {
                        "results": {
                            "products": [
                                {
                                    "id": 1,
                                    "title": "Wireless headphones",
                                    "price": "50",
                                    "url": "https://evil.example.com/products/evil",
                                },
                                {
                                    "id": 2,
                                    "title": "Wireless headphones",
                                    "price": "45",
                                    "url": "/products/headphones",
                                },
                            ]
                        }
                    }
                },
            )
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = ShopifyProvider(
            ShopifyStoreConfig(name="Audio", url="https://audio.example.com", currency="USD"), client
        )
        results = await provider.fetch("headphones")
        assert len(results) == 1 and results[0].price == 45 and results[0].rating is None


def test_shopify_sources_have_independent_progress_and_caches(settings):
    first = Source(
        [product("first", store="shopify", host="first.example.com")], "shopify:first", "First shop"
    )
    second = Source(
        [product("second", store="shopify", host="second.example.com")], "shopify:second", "Second shop"
    )
    with TestClient(
        create_app(settings.model_copy(update={"catalog_mode": "live"}), [first, second])
    ) as client:
        for _ in range(2):
            result = client.get("/api/products", params={"q": "headphones"}).json()
            assert result["total"] == 2 and {s["id"] for s in result["providers"]} == {
                "shopify:first",
                "shopify:second",
            }
            assert all(s["status"] == "ok" for s in result["providers"])
        assert first.calls == second.calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",
        "https://localhost",
        "https://shop.local",
        "http://shop.example.com",
        "https://user:pass@shop.example.com",
    ],
)
def test_shopify_configuration_rejects_local_or_unsafe_urls(url):
    with pytest.raises(ValidationError):
        ShopifyStoreConfig(name="Store", url=url, currency="USD")


async def test_shopify_rejects_dns_resolving_to_private_addresses(monkeypatch):
    import asyncio
    import socket

    async def addresses(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    provider = ShopifyProvider(
        ShopifyStoreConfig(name="Store", url="https://audio.example.com", currency="USD"), None
    )
    with pytest.raises(MarketplaceAccessError, match="publicly reachable"):
        await provider._validate_public_address()


def test_shopify_does_not_borrow_ratings_from_another_product_on_the_page():
    page = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Product",
                "name": "Unrelated product",
                "url": "/products/other",
                "aggregateRating": {"ratingValue": 5, "reviewCount": 100000},
            }
        )
        + "</script>"
    )
    assert parse_product_page(
        page.encode(), "https://audio.example.com/products/headphones", "Wireless headphones"
    ) == ("", None, None, None)


async def test_aliexpress_requests_use_the_clients_public_template_and_a_fixed_search_host():
    def transport(request):
        assert request.url.host == "www.aliexpress.com"
        assert request.url.path == "/w/wholesale-wireless-headphones.html"
        assert request.url.params["SearchText"] == "wireless headphones"
        assert request.headers["User-Agent"] == "python-httpx/" + httpx.__version__
        return httpx.Response(
            200,
            text='<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "Product",
                    "name": "Wireless headphones",
                    "sku": "123456",
                    "url": "https://www.aliexpress.com/item/123456.html",
                    "offers": {"price": 40, "priceCurrency": "USD"},
                }
            )
            + "</script>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        results = await PublicMarketplaceProvider("aliexpress", client).fetch("wireless headphones")
        assert len(results) == 1 and results[0].price == 40


def test_shopify_collects_reviews_from_later_matching_structured_data():
    records = [
        {
            "@type": "Product",
            "name": "Headphones",
            "url": "/products/headphones",
            "description": "Clear stereo",
        },
        {
            "@type": "Product",
            "name": "Headphones",
            "url": "/products/headphones",
            "aggregateRating": {"ratingValue": 4.8, "reviewCount": 468},
        },
    ]
    page = "".join('<script type="application/ld+json">' + json.dumps(item) + "</script>" for item in records)
    assert parse_product_page(
        page.encode(), "https://store.example.com/products/headphones", "Headphones"
    ) == ("Clear stereo", 4.8, 468, None)


def test_shopify_sales_and_review_widgets_are_scoped_to_the_exact_product():
    page = b"""<div class="jdgm-preview-badge" data-id="999"><span data-average-rating="5" data-number-of-reviews="99999"></span></div>
    <div class="jdgm-preview-badge" data-id="123"><span data-average-rating="4.78" data-number-of-reviews="468"></span></div>
    <div class="product-item"><a href="/products/other">Other</a><span class="feature-badge">99M Sold</span></div>
    <div class="product-item"><a href="/products/headphones">Headphones</a><span class="feature-badge">5k Sold</span></div>"""
    parsed = parse_product_page(
        page,
        "https://store.example.com/products/headphones",
        "Headphones",
        123,
        ".product-item",
        ".feature-badge",
    )
    assert parsed == ("", "4.78", "468", "5k Sold")


def test_shopify_does_not_copy_related_product_sales_when_current_card_is_missing():
    page = b'<div class="product-item"><a href="/products/other">Other</a><span class="feature-badge">99M Sold</span></div>'
    assert parse_product_page(
        page,
        "https://store.example.com/products/headphones",
        "Headphones",
        123,
        ".product-item",
        ".feature-badge",
    ) == ("", None, None, None)
