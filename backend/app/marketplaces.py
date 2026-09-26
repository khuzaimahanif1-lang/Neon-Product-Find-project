"""Server-side public-page adapters. No browser automation or access-wall bypass."""

import asyncio
import hashlib
import html
import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx
from anyio import to_thread
from bs4 import BeautifulSoup

from .schemas import CurrencyId, Offer, Product

DOMAINS = {
    "daraz": ("daraz.pk",),
    "amazon": ("amazon.com", "amazon.co.uk", "amazon.ae"),
    "alibaba": ("alibaba.com",),
    "aliexpress": ("aliexpress.com", "aliexpress.us"),
    "priceoye": ("priceoye.pk",),
    "telemart": ("telemart.pk",),
    "shopify": ("myshopify.com",),
}
SEARCH_PAGES = {
    "daraz": ("https://www.daraz.pk/catalog/", "q"),
    "amazon": ("https://www.amazon.com/s", "k"),
    "alibaba": ("https://www.alibaba.com/trade/search", "SearchText"),
    "aliexpress": ("https://www.aliexpress.com/w/wholesale.html", "SearchText"),
}


class MarketplaceAccessError(Exception):
    pass


def marketplace_url(store, value, allowed_domains=None):
    if not value:
        return None
    value = urljoin(SEARCH_PAGES.get(store, (f"https://{DOMAINS[store][0]}", ""))[0], str(value))
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    if not any(
        host == domain or host.endswith("." + domain) for domain in (allowed_domains or DOMAINS[store])
    ):
        return None
    return value


def number(value):
    if value is None:
        return None
    found = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    return float(found[0].replace(",", "")) if found else None


def count(value):
    parsed = number(value)
    if parsed is None:
        return None
    match = re.search(r"[\d.,]+\s*([kKmM])\b", str(value))
    return int(parsed * ({"k": 1000, "m": 1_000_000}[match[1].lower()] if match else 1))


def infer_art(name):
    text = name.lower()
    for terms, category, art in [
        (("earbud", "airpod", "earphone"), "audio", "earbuds"),
        (("headphone", "headset"), "audio", "headphones"),
        (("speaker",), "audio", "speaker"),
        (("laptop", "macbook", "notebook"), "computing", "laptop"),
        (("smartwatch", "watch"), "wearables", "watch"),
        (("iphone", "smartphone", "mobile phone"), "phones", "phone"),
        (("camera",), "lifestyle", "camera"),
    ]:
        if any(term in text for term in terms):
            return category, art
    return "lifestyle", "bag"


def make_product(store, listing_id, name, price, url, currency="PKR", **fields):
    price = number(price)
    url = marketplace_url(store, url, fields.get("allowed_domains"))
    if not name or not price or price > 100_000_000 or not url:
        return None
    name = html.unescape(str(name))[:200]
    category, art = infer_art(name)
    rating = number(fields.get("rating"))
    if rating is not None and not 0 <= rating <= 5:
        rating = None
    reviews = count(fields.get("reviews"))
    sold = count(fields.get("sales_label"))
    lines = fields.get("overview") or []
    if isinstance(lines, str):
        lines = [lines]
    if isinstance(lines, dict):
        lines = [f"{key}: {value}" for key, value in lines.items()]
    overview = [html.unescape(str(line))[:400] for line in lines[:20] if str(line).strip()]
    digest = hashlib.sha256(str(listing_id or url).encode()).hexdigest()[:24]
    observed = datetime.now(UTC).isoformat()
    offer = Offer(
        store=store,
        storeName=fields.get("store_name"),
        price=price,
        currency=currency,
        url=url,
        source="live",
        shippingFee=fields.get("shipping_fee"),
        seller=fields.get("seller"),
        rating=rating,
        reviews=reviews,
        soldCount=sold,
        salesLabel=fields.get("sales_label"),
        minimumOrder=fields.get("minimum_order"),
        priceLabel=fields.get("price_label"),
        observedAt=observed,
    )
    image = fields.get("image")
    if image and str(image).startswith("//"):
        image = "https:" + str(image)
    if image and not str(image).startswith(("https://", "http://")):
        image = None
    return Product(
        id=f"{store}-{digest}",
        name=name,
        brand=str(fields.get("brand") or "Unspecified")[:100],
        category=category,
        art=art,
        tags=name[:1000],
        subtitle=" · ".join(overview)[:250],
        price=price,
        oldPrice=number(fields.get("old_price")) or price,
        currency=currency,
        rating=rating,
        reviews=reviews,
        soldCount=sold,
        salesLabel=fields.get("sales_label"),
        imageUrl=image,
        overview=overview,
        offers=[offer],
        source="live",
    )


def parse_daraz(payload, limit=24):
    items = payload.get("mods", {}).get("listItems") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise MarketplaceAccessError("Daraz did not return product results. API access may be required.")
    products = []
    for item in items[:limit]:
        if item.get("inStock") is False:
            continue
        params = parse_qs(item.get("querystring", ""))
        # A promotional badge alone does not guarantee free delivery for every buyer.
        fee = 0 if params.get("freeshipping") == ["1"] else None
        product = make_product(
            "daraz",
            item.get("itemId"),
            item.get("name"),
            item.get("price"),
            item.get("itemUrl"),
            rating=item.get("ratingScore"),
            reviews=item.get("review"),
            sales_label=item.get("itemSoldCntShow"),
            seller=item.get("sellerName"),
            brand=item.get("brandName"),
            image=item.get("image"),
            old_price=item.get("originalPrice"),
            overview=item.get("description") or [],
            shipping_fee=fee,
        )
        if product:
            products.append(product)
    if any(item.get("inStock") is not False for item in items) and not products:
        raise MarketplaceAccessError("Daraz listings could not be normalized. Check the adapter.")
    return products


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def price_currency(label):
    # Amazon can localize prices to the visitor's currency, even on amazon.com.
    for code in get_args(CurrencyId):
        if code in label.upper():
            return code
    for symbol, code in (("$", "USD"), ("£", "GBP"), ("€", "EUR")):
        if symbol in label:
            return code
    return None


def parse_amazon(soup, limit=24):
    products = []
    cards = soup.select('[data-component-type="s-search-result"][data-asin]')
    for card in cards[:limit]:
        heading = card.select_one("h2")
        name = text(heading)
        link = card.select_one('a[href*="/dp/"]')
        price = card.select_one(".a-price .a-offscreen")
        currency = price_currency(text(price))
        if currency is None:
            continue
        rating = card.select_one(".a-icon-alt")
        review_link = card.select_one('a[href*="customerReviews"] span')
        if review_link is None:
            review_link = card.select_one('[data-cy="reviews-block"] .a-size-base.s-underline-text')
        image = card.select_one("img.s-image")
        sales = re.search(r"[\d.,]+[KkMm]?\+? bought in (?:past|the past) month", text(card))
        product = make_product(
            "amazon",
            card.get("data-asin"),
            name,
            text(price),
            f"https://www.amazon.com/dp/{card.get('data-asin')}" if link else None,
            currency=currency,
            rating=text(rating),
            reviews=text(review_link),
            sales_label=sales[0] if sales else None,
            image=image.get("src") if image else None,
            old_price=text(card.select_one(".a-text-price .a-offscreen")),
        )
        if product:
            products.append(product)
    if cards and not products:
        raise MarketplaceAccessError(
            "Amazon listings changed or have no published prices. Check the adapter."
        )
    if not cards and not soup.select('[data-component-type="s-search-results"]'):
        raise MarketplaceAccessError("Amazon did not expose searchable listings. API access may be required.")
    return products


def jsonld_products(value):
    if isinstance(value, list):
        for child in value:
            yield from jsonld_products(child)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if types == "Product" or isinstance(types, list) and "Product" in types:
            yield value
        for key in ("@graph", "itemListElement", "item"):
            if key in value:
                yield from jsonld_products(value[key])


def embedded_search_data(soup):
    decoder = json.JSONDecoder()
    patterns = (
        r"_init_data_\s*=\s*\{\s*data\s*:\s*",
        r"(?:window\.)?(?:runParams|_dida_config_)\s*=\s*",
    )
    for script in soup.select("script"):
        body = script.string or script.get_text()
        if script.get("type") == "application/json" or script.get("id") == "__NEXT_DATA__":
            try:
                yield json.loads(body)
            except ValueError:
                pass
        for pattern in patterns:
            for match in re.finditer(pattern, body):
                try:
                    yield decoder.raw_decode(body[match.end() :].lstrip())[0]
                except ValueError:
                    pass


def parse_aliexpress(soup, limit=24):
    products, seen = [], set()
    for data in embedded_search_data(soup):
        pending = [(data, 0)]
        visited = 0
        while pending and visited < 20_000:
            value, depth = pending.pop()
            visited += 1
            if depth > 18:
                continue
            if isinstance(value, list):
                pending.extend((child, depth + 1) for child in reversed(value))
                continue
            if not isinstance(value, dict):
                continue
            listing_id = str(value.get("productId") or value.get("itemId") or "")
            if re.fullmatch(r"\d{6,20}", listing_id) and listing_id not in seen:
                title = value.get("title") or value.get("productTitle") or {}
                name = title.get("displayTitle") or title.get("text") if isinstance(title, dict) else title
                prices = value.get("prices") or {}
                sale = prices.get("salePrice") or {}
                currency = sale.get("currencyCode") or price_currency(str(sale.get("formattedPrice", "")))
                if currency in get_args(CurrencyId):
                    evaluation, trade = value.get("evaluation") or {}, value.get("trade") or {}
                    image = value.get("image") or {}
                    points = [
                        point.get("tagContent", {}).get("tagText") for point in value.get("sellingPoints", [])
                    ]
                    product = make_product(
                        "aliexpress",
                        listing_id,
                        name,
                        sale.get("minPrice") or sale.get("formattedPrice"),
                        f"https://www.aliexpress.com/item/{listing_id}.html",
                        currency=currency,
                        rating=evaluation.get("starRating"),
                        reviews=evaluation.get("totalValidNum"),
                        sales_label=trade.get("tradeDesc"),
                        seller=(value.get("shopInfo") or {}).get("storeName"),
                        image=image.get("imgUrl") if isinstance(image, dict) else image,
                        old_price=(prices.get("originalPrice") or {}).get("minPrice"),
                        overview=[point for point in points if point],
                        price_label="Search price; confirm variant and promotion eligibility",
                    )
                    if product:
                        products.append(product)
                        seen.add(listing_id)
                        if len(products) >= limit:
                            return products
            pending.extend((child, depth + 1) for child in reversed(list(value.values())))
    return products or parse_structured_marketplace(soup, "aliexpress", limit)


def parse_structured_marketplace(soup, store, limit=24):
    products = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            values = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for raw in jsonld_products(values):
            offers = raw.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                continue
            if str(offers.get("availability", "")).endswith("OutOfStock"):
                continue
            rating = raw.get("aggregateRating") or {}
            brand = raw.get("brand") or {}
            image = raw.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            currency = offers.get("priceCurrency", "USD")
            if currency not in get_args(CurrencyId):
                continue
            product = make_product(
                store,
                raw.get("sku") or raw.get("url"),
                raw.get("name"),
                offers.get("price") or offers.get("lowPrice"),
                offers.get("url") or raw.get("url"),
                currency=currency,
                brand=brand.get("name") if isinstance(brand, dict) else brand,
                rating=rating.get("ratingValue"),
                reviews=rating.get("reviewCount") or rating.get("ratingCount"),
                image=image,
                overview=[text(BeautifulSoup(raw.get("description", ""), "lxml"))[:400]],
                price_label="Starting price; confirm variant, quantity and delivery on the marketplace",
            )
            if product:
                products.append(product)
    if not products:
        raise MarketplaceAccessError(f"{store.title()} requires API access or did not expose product data.")
    return products[:limit]


def parse_page(body, store, limit):
    if store == "daraz":
        try:
            return parse_daraz(json.loads(body), limit)
        except json.JSONDecodeError as error:
            raise MarketplaceAccessError("Daraz returned an access page instead of product data.") from error
    soup = BeautifulSoup(body, "lxml")
    if soup.select('#captchacharacters, #nocaptcha, #nc_1_wrapper, form[action*="validateCaptcha"]'):
        raise MarketplaceAccessError(
            f"{store.title()} is blocking automated access. Connect a permitted API source."
        )
    if store == "amazon":
        return parse_amazon(soup, limit)
    if store == "aliexpress":
        return parse_aliexpress(soup, limit)
    return parse_structured_marketplace(soup, store, limit)


class PublicMarketplaceProvider:
    def __init__(self, store, client, limit=24):
        self.config = SimpleNamespace(store=store)
        self.client, self.limit = client, limit

    async def fetch(self, query):
        endpoint, parameter = SEARCH_PAGES[self.config.store]
        if self.config.store == "aliexpress":
            endpoint = (
                "https://www.aliexpress.com/w/wholesale-" + quote(query.replace(" ", "-"), safe="-") + ".html"
            )
        params = {parameter: query}
        if self.config.store == "daraz":
            params["ajax"] = "true"
        headers = {
            "User-Agent": "NeonFind/1.0 (public product comparison)",
            "Accept": "application/json,text/html",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self.config.store == "aliexpress":
            # AliExpress serves a different public template for the branded user-agent.
            headers["User-Agent"] = self.client.headers.get("User-Agent", "python-httpx/" + httpx.__version__)
        async with self.client.stream(
            "GET",
            endpoint,
            params=params,
            headers=headers,
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308, 401, 403, 429, 503}:
                raise MarketplaceAccessError(
                    f"{self.config.store.title()} restricted this request. Try later or connect an API source."
                )
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 3_000_000:
                    raise MarketplaceAccessError("Marketplace response exceeded the size limit.")
        return await to_thread.run_sync(parse_page, bytes(body), self.config.store, self.limit)


class CurrencyService:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings
        self.rates = {**settings.currency_rates, settings.search_currency: 1.0}
        self.metadata = {"base": settings.search_currency, "source": "configured", "approximate": True}
        self.expires = 0.0
        self.lock = asyncio.Lock()

    async def refresh(self):
        async with self.lock:
            await self._refresh()

    async def _refresh(self):
        import asyncio
        import math
        import time

        if not self.settings.fetch_exchange_rates or time.monotonic() < self.expires:
            return
        self.expires = time.monotonic() + 60
        try:
            async with asyncio.timeout(4):
                response = await self.client.get(
                    "https://open.er-api.com/v6/latest/" + self.settings.search_currency
                )
                response.raise_for_status()
                payload = response.json()
            if (
                payload.get("result") != "success"
                or payload.get("base_code") != self.settings.search_currency
            ):
                return
            # Upstream rates are units per base; ranking requires base units per foreign unit.
            self.rates.update(
                {
                    key: 1 / rate
                    for key, rate in payload["rates"].items()
                    if isinstance(rate, (float, int)) and math.isfinite(rate) and rate > 0
                }
            )
            self.metadata = {
                "base": self.settings.search_currency,
                "source": "ExchangeRate-API",
                "updatedAt": payload.get("time_last_update_utc"),
                "approximate": True,
            }
            self.expires = time.monotonic() + 3600
        except (httpx.HTTPError, ValueError, KeyError, TimeoutError):
            # Keep original currencies; never invent an exchange rate.
            self.expires = time.monotonic() + 60
