"""Search explicitly configured public Shopify storefronts, without order/admin access."""

import asyncio
import ipaddress
import json
import re
import socket
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

from anyio import to_thread
from bs4 import BeautifulSoup

from .marketplaces import MarketplaceAccessError, jsonld_products, make_product, marketplace_url, text


class ShopifyProvider:
    def __init__(self, store, client, limit=24):
        self.store, self.client, self.limit = store, client, min(limit, 10)
        self.base_url = str(store.url).rstrip("/") + "/"
        self.host = store.url.host.lower()
        self.config = SimpleNamespace(
            store="shopify",
            source_id="shopify:" + self.base_url,
            display_name="Shopify · " + store.name,
            metric_notice="Sales and ratings are included only when published for the same listing.",
        )
        self.detail_slots = asyncio.Semaphore(3)

    async def _validate_public_address(self):
        # Store URLs are backend configuration; product responses cannot select a host.
        addresses = await asyncio.get_running_loop().getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise MarketplaceAccessError("Configure a publicly reachable Shopify storefront.")

    async def _get(self, url, params=None, accept="application/json"):
        async with self.client.stream("GET", url, params=params, headers={"Accept": accept}) as response:
            if response.status_code in {301, 302, 303, 307, 308, 401, 403, 429, 503}:
                raise MarketplaceAccessError(
                    "This Shopify store restricted public search. Connect its permitted data source."
                )
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise MarketplaceAccessError("Shopify response exceeded the size limit.")
            return bytes(body)

    async def _product(self, raw):
        if raw.get("available") is False:
            return None
        url = marketplace_url("shopify", urljoin(self.base_url, raw.get("url") or ""), [self.host])
        if not url or urlsplit(url).hostname != self.host or "/products/" not in urlsplit(url).path:
            return None
        description, rating, reviews, sales = "", None, None, None
        try:
            async with asyncio.timeout(5), self.detail_slots:
                page = await self._get(url, accept="text/html")
            description, rating, reviews, sales = await to_thread.run_sync(
                parse_product_page,
                page,
                url,
                raw.get("title"),
                raw.get("id"),
                self.store.product_card_selector,
                self.store.sales_badge_selector,
            )
        except Exception:
            # Public listing remains usable when extra description/review data is unavailable.
            pass
        image = raw.get("image") or (raw.get("featured_image") or {}).get("url")
        return make_product(
            "shopify",
            self.host + ":" + str(raw.get("id") or url),
            raw.get("title"),
            raw.get("price"),
            url,
            currency=self.store.currency,
            allowed_domains=[self.host],
            store_name=self.store.name,
            seller=self.store.name,
            brand=raw.get("vendor"),
            rating=rating,
            sales_label=sales,
            reviews=reviews,
            image=image,
            old_price=raw.get("compare_at_price_max"),
            overview=[description] if description else [],
            price_label="Confirm selected variant and final delivery on this store",
        )

    async def fetch(self, query):
        await self._validate_public_address()
        body = await self._get(
            urljoin(self.base_url, "search/suggest.json"),
            {
                "q": query,
                "resources[type]": "product",
                "resources[limit]": self.limit,
                "resources[options][unavailable_products]": "hide",
            },
        )
        try:
            raw = json.loads(body)["resources"]["results"]["products"]
        except (ValueError, TypeError, KeyError) as error:
            raise MarketplaceAccessError("This store did not expose Shopify predictive search.") from error
        if not isinstance(raw, list):
            raise MarketplaceAccessError("This store returned an unsupported product response.")
        # Predictive Search uses decimal major currency units, unlike products/<handle>.js.
        tasks = [
            asyncio.create_task(self._product(item)) for item in raw[: self.limit] if isinstance(item, dict)
        ]
        try:
            return [product for product in await asyncio.gather(*tasks) if product]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def parse_product_page(body, url, name, listing_id=None, card_selector=None, badge_selector=None):
    soup = BeautifulSoup(body, "lxml")
    description, rating, reviews, sales = "", None, None, None
    current_path = urlsplit(url).path.rstrip("/")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except ValueError:
            continue
        for product in jsonld_products(data):
            offers = product.get("offers") or {}
            source_url = product.get("url") or (offers.get("url") if isinstance(offers, dict) else None)
            if source_url:
                if urlsplit(urljoin(url, source_url)).path.rstrip("/") != current_path:
                    continue
            elif product.get("name") != name:
                continue
            description = (
                description or text(BeautifulSoup(str(product.get("description") or ""), "lxml"))[:400]
            )
            aggregate = product.get("aggregateRating") or {}
            if isinstance(aggregate, dict) and aggregate.get("ratingValue") is not None:
                rating = aggregate.get("ratingValue")
                reviews = aggregate.get("reviewCount") or aggregate.get("ratingCount")

    # Public review widgets must match the exact native Shopify product ID.
    if str(listing_id).isdigit() and rating is None:
        for widget in soup.select(f'.jdgm-preview-badge[data-id="{listing_id}"]'):
            badge = widget.select_one("[data-average-rating][data-number-of-reviews]")
            if badge:
                rating, reviews = badge.get("data-average-rating"), badge.get("data-number-of-reviews")
                break
        if rating is None:
            widget = soup.select_one(f'.loox-rating[data-id="{listing_id}"][data-rating][data-raters]')
            if widget:
                rating, reviews = widget.get("data-rating"), widget.get("data-raters")

    # Optional store-specific card selectors extract labels only from this product's card.
    if card_selector and badge_selector:
        for card in soup.select(card_selector):
            if not any(
                urlsplit(urljoin(url, link.get("href"))).path.rstrip("/") == current_path
                for link in card.select("a[href]")
            ):
                continue
            for badge in card.select(badge_selector):
                match = re.search(r"\b[\d.,]+\s*[kKmM]?\+?\s*Sold\b", text(badge), re.IGNORECASE)
                if match:
                    sales = match[0]
                    break
            if sales:
                break
    return description, rating, reviews, sales
