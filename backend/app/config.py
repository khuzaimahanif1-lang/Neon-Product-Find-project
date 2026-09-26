from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .schemas import CurrencyId

BACKEND_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
STORE_NAMES = {
    "daraz": "Daraz",
    "amazon": "Amazon",
    "alibaba": "Alibaba",
    "aliexpress": "AliExpress",
    "priceoye": "PriceOye",
    "telemart": "Telemart",
    "shopify": "Shopify",
}


class ShopifyStoreConfig(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl
    currency: CurrencyId
    product_card_selector: str | None = Field(default=None, max_length=120)
    sales_badge_selector: str | None = Field(default=None, max_length=120)

    @field_validator("url")
    @classmethod
    def public_store_url(cls, value):
        import ipaddress

        host = value.host.lower()
        if value.scheme != "https" or value.username or value.password or value.query or value.fragment:
            raise ValueError("Shopify store URLs require HTTPS without credentials, queries or fragments.")
        if "." not in host or host.endswith((".local", ".internal", ".localhost")) or host == "localhost":
            raise ValueError("Configure a public Shopify storefront domain.")
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return value
        if not address.is_global:
            raise ValueError("Shopify stores cannot use private or local IP addresses.")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEONFIND_", env_file=BACKEND_DIR / ".env", extra="ignore")
    environment: Literal["development", "production"] = "development"
    catalog_mode: Literal["demo", "live"] = "live"
    public_marketplaces: list[str] = ["daraz", "amazon", "alibaba", "aliexpress"]
    shopify_stores: list[ShopifyStoreConfig] = Field(
        default_factory=lambda: [
            ShopifyStoreConfig(
                name="Audionic",
                url="https://audionic.co",
                currency="PKR",
                product_card_selector=".product-item",
                sales_badge_selector=".feature-badge",
            )
        ],
        max_length=8,
    )
    search_currency: str = "PKR"
    currency_rates: dict[str, float] = {"PKR": 1.0}
    fetch_exchange_rates: bool = True
    marketplace_limit: int = Field(default=24, ge=1, le=40)
    database_url: str = f"sqlite+aiosqlite:///{(BACKEND_DIR / 'data' / 'neonfind.db').as_posix()}"
    jwt_secret: SecretStr | None = None
    session_minutes: int = Field(default=60, ge=5, le=1440)
    port: int = Field(default=8001, ge=1024, le=65535)
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]
    cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    provider_timeout_seconds: float = Field(default=20, ge=1, le=30)
    provider_config: Path = BACKEND_DIR / "providers.json"
    auth_limit_per_minute: int = Field(default=12, ge=1)
    search_limit_per_minute: int = Field(default=120, ge=1)
    websocket_limit_per_minute: int = Field(default=30, ge=1)

    @field_validator("public_marketplaces")
    @classmethod
    def supported_public_stores(cls, value):
        if any(store not in {"daraz", "amazon", "alibaba", "aliexpress"} for store in value):
            raise ValueError("Public adapters support Daraz, Amazon, Alibaba and AliExpress.")
        return list(dict.fromkeys(value))

    @field_validator("currency_rates")
    @classmethod
    def valid_currency_rates(cls, value):
        import math

        if any(not math.isfinite(rate) or rate <= 0 for rate in value.values()):
            raise ValueError("Currency rates must be positive finite numbers.")
        return value

    @field_validator("database_url")
    @classmethod
    def supported_database(cls, value):
        if not value.startswith("sqlite+aiosqlite:///"):
            raise ValueError(
                "This MVP supports sqlite+aiosqlite database URLs. PostgreSQL needs its own upsert adapter."
            )
        return value

    @model_validator(mode="after")
    def validate_security(self):
        if self.jwt_secret and len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("NEONFIND_JWT_SECRET must contain at least 32 characters.")
        if self.environment == "production" and not self.jwt_secret:
            raise ValueError("Production requires NEONFIND_JWT_SECRET.")
        if "*" in self.allowed_origins:
            raise ValueError("Use explicit allowed origins for credentialed requests.")
        return self
