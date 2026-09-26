import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.marketplaces import CurrencyService, MarketplaceAccessError, parse_page
from app.recommendations import rank_products
from app.schemas import Product

ORIGIN = {"origin": "http://localhost:8001"}


def live_product(store="daraz", name="Wireless headphones", price=998, currency="PKR"):
    return Product.model_validate(
        {
            "id": store + "-headphones",
            "name": name,
            "category": "audio",
            "source": "live",
            "currency": currency,
            "rating": 4.7,
            "reviews": 110,
            "soldCount": 745,
            "offers": [
                {
                    "store": store,
                    "price": price,
                    "shippingFee": None,
                    "currency": currency,
                    "source": "live",
                    "url": f"https://www.{store}.{'pk' if store == 'daraz' else 'com'}/products/headphones",
                    "soldCount": 745,
                    "salesLabel": "745 sold",
                }
            ],
        }
    )


class Provider:
    def __init__(self, store, delay=0, fail=False):
        from types import SimpleNamespace

        self.config = SimpleNamespace(store=store)
        self.delay, self.fail, self.cancelled = delay, fail, False

    async def fetch(self, query):
        try:
            await asyncio.sleep(self.delay)
            if self.fail:
                raise MarketplaceAccessError("Source requires API access.")
            return [live_product(self.config.store, name=query + " headphones")]
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def live_settings(settings, **values):
    return settings.model_copy(update={"catalog_mode": "live", **values})


def collect(socket):
    events = []
    while True:
        event = socket.receive_json()
        events.append(event)
        if event["type"] in {"complete", "error"}:
            return events


def test_no_query_returns_no_products_and_does_not_fetch(settings):
    provider = Provider("daraz", fail=True)
    with TestClient(create_app(live_settings(settings), [provider])) as client:
        for query in ("", "   "):
            response = client.get("/api/products", params={"q": query})
            assert response.status_code == 200 and response.json()["products"] == []


def test_socket_streams_partial_results_before_slow_source_completes(settings):
    with TestClient(
        create_app(live_settings(settings), [Provider("daraz"), Provider("amazon", delay=0.1)])
    ) as client:
        with client.websocket_connect("/api/ws/search", headers=ORIGIN) as socket:
            socket.send_json({"requestId": "one", "q": "wireless"})
            started = socket.receive_json()
            first = socket.receive_json()
            assert started["type"] == "started" and started["products"] == []
            assert first["type"] == "update" and len(first["products"]) == 1
            assert any(p["status"] == "searching" for p in first["providers"])
            assert client.get("/api/products/" + first["products"][0]["id"]).status_code == 200
            rest = collect(socket)
            assert rest[-1]["type"] == "complete" and rest[-1]["total"] == 2
            assert all(e["requestId"] == "one" for e in rest)


def test_socket_keeps_healthy_products_when_another_source_is_blocked(settings):
    with TestClient(
        create_app(live_settings(settings), [Provider("daraz"), Provider("alibaba", fail=True)])
    ) as client:
        with client.websocket_connect("/api/ws/search", headers=ORIGIN) as socket:
            socket.send_json({"requestId": "partial", "q": "wireless"})
            final = collect(socket)[-1]
            assert final["partial"] and final["total"] == 1
            assert any(p.get("code") == "access_restricted" for p in final["providers"])
            assert final["products"][0]["offers"][0]["url"].startswith("https://www.daraz.pk/")


def test_socket_reports_all_failed_sources_without_sample_fallback(settings):
    with TestClient(create_app(live_settings(settings), [Provider("daraz", fail=True)])) as client:
        with client.websocket_connect("/api/ws/search", headers=ORIGIN) as socket:
            socket.send_json({"requestId": "failure", "q": "wireless"})
            events = collect(socket)
            assert events[-1]["type"] == "error"
            assert all(not e.get("products") for e in events)


def test_socket_rejects_foreign_origin(settings):
    with TestClient(create_app(live_settings(settings), [])) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/ws/search", headers={"origin": "https://foreign.example"}):
                pass


def test_socket_validates_input_and_applies_rate_limit(settings):
    with TestClient(
        create_app(live_settings(settings, websocket_limit_per_minute=1), [Provider("daraz")])
    ) as client:
        with client.websocket_connect("/api/ws/search", headers=ORIGIN) as socket:
            socket.send_json({"requestId": "bad", "q": "   "})
            assert socket.receive_json()["type"] == "error"
            socket.send_json({"requestId": "first", "q": "wireless", "freeShipping": True})
            assert collect(socket)[-1]["products"] == []  # Unknown delivery is not free.
            socket.send_json({"requestId": "limited", "q": "wireless"})
            assert "Too many" in socket.receive_json()["message"]


def test_replacement_search_cancels_previous_provider_work(settings):
    slow = Provider("daraz", delay=0.2)
    with TestClient(create_app(live_settings(settings), [slow])) as client:
        with client.websocket_connect("/api/ws/search", headers=ORIGIN) as socket:
            socket.send_json({"requestId": "old", "q": "old"})
            assert socket.receive_json()["type"] == "started"
            socket.send_json({"requestId": "new", "q": "new"})
            events = collect(socket)
            assert all(e["requestId"] == "new" for e in events)
            assert events[-1]["products"][0]["name"] == "new headphones"


def test_daraz_parser_preserves_published_sales_and_unknown_delivery():
    payload = {
        "mods": {
            "listItems": [
                {
                    "itemId": "123",
                    "name": "Wireless headphones",
                    "price": "998",
                    "originalPrice": "3000",
                    "itemUrl": "//www.daraz.pk/products/wireless-i123.html",
                    "ratingScore": "4.675",
                    "review": "110",
                    "itemSoldCntShow": "745 sold",
                    "sellerName": "Example seller",
                    "description": ["Deep bass", "Bluetooth"],
                    "querystring": "freeshipping=0",
                }
            ]
        }
    }
    product = parse_page(json.dumps(payload).encode(), "daraz", 24)[0]
    assert product.sold_count == 745 and product.rating == 4.675 and product.reviews == 110
    assert product.overview == ["Deep bass", "Bluetooth"]
    assert product.offers[0].shipping_fee is None and str(product.offers[0].url).startswith(
        "https://www.daraz.pk/"
    )


def test_amazon_parser_keeps_fractional_usd_price_and_monthly_sales_label():
    body = b"""<div data-component-type="s-search-result" data-asin="B012345678">
    <h2>Wireless headphones</h2><a href="/dp/B012345678">Details</a>
    <span class="a-price"><span class="a-offscreen">$79.99</span></span>
    <span class="a-icon-alt">4.5 out of 5 stars</span>
    <a href="/dp/B012345678#customerReviews"><span>1,200</span></a>
    <span>10K+ bought in past month</span></div>"""
    product = parse_page(body, "amazon", 24)[0]
    assert product.price == 79.99 and product.currency == "USD"
    assert product.sold_count == 10000 and product.sales_label == "10K+ bought in past month"
    assert product.offers[0].total_price is None


def test_alibaba_challenge_is_not_a_product_result():
    with pytest.raises(MarketplaceAccessError, match="blocking"):
        parse_page(b'<html><div id="nocaptcha"></div></html>', "alibaba", 24)


def test_unknown_delivery_and_missing_conversion_do_not_invent_total():
    product = live_product("amazon", price=79.99, currency="USD")
    ranked = rank_products([product], rates={"USD": 280})[0]
    assert ranked.comparison_price == 22397.2
    assert ranked.offers[0].total_price is None and ranked.price_basis.endswith("delivery unknown")
    assert not rank_products([product], free_shipping=True)
    assert not rank_products([product], max_price=100000)  # No USD rate configured.


async def test_currency_rates_use_verified_upstream_base_and_inverse(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "result": "success",
                    "base_code": "PKR",
                    "rates": {"PKR": 1, "USD": 0.004},
                    "time_last_update_utc": "published update",
                },
            )
        )
    ) as client:
        service = CurrencyService(client, settings.model_copy(update={"fetch_exchange_rates": True}))
        await service.refresh()
        assert service.rates["USD"] == 250
        assert service.metadata["source"] == "ExchangeRate-API"


def test_amazon_localized_price_is_not_assumed_to_be_usd():
    body = b"""<div data-component-type="s-search-result" data-asin="B012345678">
    <h2>Wireless headphones</h2><a href="/dp/B012345678">Details</a>
    <span class="a-price"><span class="a-offscreen">PKR 5,546.17</span></span></div>"""
    product = parse_page(body, "amazon", 24)[0]
    assert product.price == 5546.17 and product.currency == "PKR"
    assert rank_products([product], rates={"USD": 280})[0].comparison_price == 5546.17
