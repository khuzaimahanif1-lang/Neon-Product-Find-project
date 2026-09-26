import asyncio
import json
import os
from pathlib import Path

import httpx
import structlog
from cachetools import TTLCache
from dotenv import dotenv_values
from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, field_validator
from sqlalchemy.dialects.sqlite import insert
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from .config import BACKEND_DIR, STORE_NAMES
from .database import load_catalog
from .marketplaces import DOMAINS, CurrencyService, MarketplaceAccessError, PublicMarketplaceProvider
from .models import CatalogProduct, utcnow
from .recommendations import normalize_query
from .schemas import Product, StoreId
from .shopify import ShopifyProvider

log = structlog.get_logger()
STORE_DOMAINS = DOMAINS


class ProviderConfig(BaseModel):
    store: StoreId
    enabled: bool = False
    endpoint: HttpUrl
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    auth_header: str = Field(default="Authorization", pattern=r"^[a-zA-Z0-9-]+$")
    auth_prefix: str = Field(default="Bearer ", max_length=32)
    query_param: str = Field(default="q", pattern=r"^[a-zA-Z0-9_-]+$")
    allowed_domains: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, value):
        import re

        if any(
            not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", domain) or "." not in domain
            for domain in value
        ):
            raise ValueError("Allowed domains must be explicit hostnames, without URLs or wildcards.")
        return [domain.lower() for domain in value]

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value):
        if value.scheme != "https" or value.username or value.password:
            raise ValueError("Provider endpoints must use HTTPS without embedded credentials.")
        return value

    @field_validator("auth_prefix")
    @classmethod
    def validate_header_prefix(cls, value):
        if "\r" in value or "\n" in value:
            raise ValueError("Header prefixes cannot contain newlines.")
        return value


def read_provider_config(path: Path):
    if not path.is_absolute():
        from .config import BACKEND_DIR

        path = BACKEND_DIR / path
    if not path.exists():
        return []
    configs = TypeAdapter(list[ProviderConfig]).validate_json(path.read_text(encoding="utf-8"))
    if len({config.store for config in configs}) != len(configs):
        raise ValueError("Configure at most one adapter per store.")
    return configs


def transient_failure(error):
    return isinstance(error, httpx.TransportError) or (
        isinstance(error, httpx.HTTPStatusError)
        and (error.response.status_code == 429 or error.response.status_code >= 500)
    )


class JsonApiProvider:
    """Adapter contract: approved API returns {products: [normalized products]}.

    A provider-specific adapter can override fetch when its API has a different
    signing scheme or response format. This adapter does not scrape HTML.
    """

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        env_values = dotenv_values(BACKEND_DIR / ".env")
        self.api_key = (
            (os.environ.get(config.api_key_env) or env_values.get(config.api_key_env))
            if config.api_key_env
            else None
        )

    async def fetch(self, query: str):
        headers = {"Accept": "application/json"}
        if self.config.api_key_env:
            key = self.api_key
            if not key:
                raise ValueError("Provider credentials are not configured.")
            headers[self.config.auth_header] = self.config.auth_prefix + key
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(2),
            wait=wait_random_exponential(multiplier=0.15, max=0.5),
            retry=retry_if_exception(transient_failure),
            reraise=True,
        ):
            with attempt:
                async with self.client.stream(
                    "GET", str(self.config.endpoint), params={self.config.query_param: query}, headers=headers
                ) as response:
                    response.raise_for_status()
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ValueError("Provider response exceeds the size limit.")
                        chunks.append(chunk)
                    payload = json.loads(b"".join(chunks))
                raw_products = payload.get("products") if isinstance(payload, dict) else None
                if not isinstance(raw_products, list) or len(raw_products) > 100:
                    raise ValueError("Provider must return a products array with at most 100 entries.")
                products = TypeAdapter(list[Product]).validate_python(raw_products)
                for product in products:
                    product.source = "live"
                    product.offers = [offer for offer in product.offers if offer.store == self.config.store]
                    if not product.offers:
                        raise ValueError("Product has no offer for the configured store.")
                    for offer in product.offers:
                        offer.source = "live"
                        if offer.url:
                            hostname = offer.url.host.lower()
                            if not any(
                                hostname == domain or hostname.endswith("." + domain)
                                for domain in (
                                    self.config.allowed_domains
                                    if self.config.store == "shopify" and self.config.allowed_domains
                                    else STORE_DOMAINS[self.config.store]
                                )
                            ):
                                raise ValueError("Offer URL must point to the configured marketplace.")
                return products
        return []


def merge_products(groups):
    merged = {}
    for group in groups:
        for original in group:
            product = original.model_copy(deep=True)
            if product.id not in merged:
                merged[product.id] = product
                continue
            existing = merged[product.id]
            if normalize_query(existing.name) != normalize_query(product.name) or normalize_query(
                existing.brand
            ) != normalize_query(product.brand):
                raise ValueError("Providers returned conflicting canonical product identifiers.")

            def offer_id(offer):
                return (offer.store, offer.url.host if offer.store == "shopify" and offer.url else "")

            offers = {offer_id(offer): offer for offer in existing.offers}
            for offer in product.offers:
                previous = offers.get(offer_id(offer))
                if (
                    previous is None
                    or offer.currency == previous.currency
                    and offer.price + (offer.shipping_fee or 0)
                    < previous.price + (previous.shipping_fee or 0)
                ):
                    offers[offer_id(offer)] = offer
            existing.offers = list(offers.values())
            if (product.reviews or 0) > (existing.reviews or 0):
                existing.rating, existing.reviews = product.rating, product.reviews
            existing.old_price = max(existing.old_price, product.old_price)
    return list(merged.values())


class CatalogService:
    def __init__(self, settings, sessions, client, adapters=None):
        self.settings, self.sessions = settings, sessions
        self.providers = (
            adapters
            if adapters is not None
            else [
                JsonApiProvider(config, client)
                for config in read_provider_config(settings.provider_config)
                if config.enabled
            ]
        )
        self.cache = TTLCache(maxsize=256, ttl=settings.cache_ttl_seconds)
        self.locks = TTLCache(maxsize=256, ttl=60)
        self.currency = CurrencyService(client, settings)
        self.fetch_slots = asyncio.Semaphore(6)
        self.provider_cache = TTLCache(maxsize=768, ttl=settings.cache_ttl_seconds)
        if adapters is None and settings.catalog_mode == "live":
            configured = {provider.config.store for provider in self.providers}
            self.providers.extend(
                PublicMarketplaceProvider(store, client, settings.marketplace_limit)
                for store in settings.public_marketplaces
                if store not in configured
            )
            if "shopify" not in configured:
                self.providers.extend(
                    ShopifyProvider(store, client, settings.marketplace_limit)
                    for store in settings.shopify_stores
                )
        self.status = {
            store: {"store": store, "name": name, "status": "unconfigured"}
            for store, name in STORE_NAMES.items()
        }
        for provider in self.providers:
            identity = getattr(provider.config, "source_id", provider.config.store)
            if provider.config.store == "shopify":
                self.status.pop("shopify", None)
            self.status[identity] = {
                "id": identity,
                "store": provider.config.store,
                "name": getattr(provider.config, "display_name", STORE_NAMES[provider.config.store]),
                "status": "ready",
            }

    async def _fetch_provider(self, provider, query):
        store = provider.config.store
        identity = getattr(provider.config, "source_id", store)
        name = getattr(provider.config, "display_name", STORE_NAMES[store])
        key = (identity, query)
        if key in self.provider_cache:
            products = self.provider_cache[key]
            return products, {
                "store": store,
                "id": identity,
                "name": name,
                "status": "ok",
                "count": len(products),
                "cached": True,
                "notice": getattr(provider.config, "metric_notice", None),
            }
        try:
            async with self.fetch_slots, asyncio.timeout(self.settings.provider_timeout_seconds):
                products = await provider.fetch(query)
            status = {"id": identity, "store": store, "name": name, "status": "ok", "count": len(products)}
            self.provider_cache[key] = products
            status["notice"] = getattr(provider.config, "metric_notice", None)
            return products, status
        except Exception as error:
            # Do not log exception strings, endpoints or headers: upstream errors can contain secrets.
            log.warning("provider_failed", store=store, error_type=type(error).__name__)
            return [], {
                "store": store,
                "id": identity,
                "name": name,
                "status": "error",
                "message": str(error)
                if isinstance(error, MarketplaceAccessError)
                else f"{STORE_NAMES[store]} timed out. Please try again."
                if isinstance(error, (TimeoutError, httpx.TimeoutException))
                else "This provider is temporarily unavailable or misconfigured.",
                "code": "access_restricted"
                if isinstance(error, MarketplaceAccessError)
                else "timeout"
                if isinstance(error, (TimeoutError, httpx.TimeoutException))
                else "unavailable",
            }

    async def remember(self, products):
        async with self.sessions() as session:
            for product in products:
                values = {
                    "id": product.id,
                    "name": product.name,
                    "category": product.category,
                    "source": product.source,
                    "payload": product.model_dump(mode="json", by_alias=True),
                    "updated_at": utcnow(),
                }
                await session.execute(
                    insert(CatalogProduct)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_={key: value for key, value in values.items() if key != "id"},
                    )
                )
            await session.commit()

    async def events(self, query):
        key = normalize_query(query)
        pending = [
            {
                "id": getattr(p.config, "source_id", p.config.store),
                "store": p.config.store,
                "name": getattr(p.config, "display_name", STORE_NAMES[p.config.store]),
                "status": "searching",
            }
            for p in self.providers
        ]
        yield {"type": "started", "products": [], "providers": pending, "cacheHit": False}
        if not key:
            yield {"type": "complete", "products": [], "providers": [], "cacheHit": False}
            return
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self.currency.refresh()
            if key in self.cache:
                products, statuses = self.cache[key]
                yield {"type": "complete", "products": products, "providers": statuses, "cacheHit": True}
                return
            if self.settings.catalog_mode == "demo":
                async with self.sessions() as session:
                    products = await load_catalog(session, "demo")
                statuses = [
                    {"store": store, "name": name, "status": "demo"} for store, name in STORE_NAMES.items()
                ]
            else:
                if not self.providers:
                    raise CatalogUnavailable(
                        "Live mode has no enabled provider. Configure a marketplace source."
                    )
                tasks = [
                    asyncio.create_task(self._fetch_provider(provider, key)) for provider in self.providers
                ]
                statuses = pending
                groups = []
                products = []
                try:
                    for task in asyncio.as_completed(tasks):
                        group, status = await task
                        try:
                            combined = merge_products([*groups, group])
                        except ValueError:
                            group = []
                            combined = products
                            status = {
                                **status,
                                "status": "error",
                                "message": "Conflicting product identity; source excluded.",
                            }
                        groups.append(group)
                        products = combined
                        statuses = [status if item["id"] == status["id"] else item for item in statuses]
                        self.status[status["id"]] = status
                        if group:
                            await self.remember(products)
                        yield {
                            "type": "update",
                            "products": products,
                            "providers": statuses,
                            "provider": status,
                            "cacheHit": False,
                        }
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                if all(status["status"] == "error" for status in statuses):
                    raise CatalogUnavailable(
                        "No marketplace could return products. Check source statuses or connect a permitted API."
                    )
            if not any(status["status"] == "error" for status in statuses):
                self.cache[key] = (products, statuses)
            yield {"type": "complete", "products": products, "providers": statuses, "cacheHit": False}

    async def catalog(self, query):
        async for event in self.events(query):
            if event["type"] == "complete":
                return event["products"], event["providers"], event["cacheHit"]


class CatalogUnavailable(Exception):
    pass
