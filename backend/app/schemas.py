from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

StoreId = Literal["daraz", "amazon", "alibaba", "aliexpress", "priceoye", "telemart", "shopify"]
CurrencyId = Literal[
    "PKR", "USD", "CNY", "GBP", "AED", "EUR", "AUD", "CAD", "INR", "BDT", "JPY", "SGD", "NPR"
]
CategoryId = Literal["audio", "phones", "computing", "wearables", "lifestyle"]


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Enter a name with at least two characters.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    email: str
    demo: bool = False


class Offer(BaseModel):
    model_config = ConfigDict(populate_by_name=True, allow_inf_nan=False)
    store: StoreId
    store_name: str | None = Field(default=None, alias="storeName", max_length=100)
    price: float = Field(gt=0, le=100_000_000, allow_inf_nan=False)
    shipping_fee: float | None = Field(
        default=None, alias="shippingFee", ge=0, le=10_000_000, allow_inf_nan=False
    )
    shipping: str = Field(default="Delivery cost confirmed at checkout", max_length=120)
    delivery: str = Field(default="Delivery estimate unavailable", max_length=120)
    currency: CurrencyId = "PKR"
    url: HttpUrl | None = None
    source: Literal["demo", "live"] = "demo"
    total_price: float | None = Field(default=None, alias="totalPrice", ge=0)
    seller: str | None = Field(default=None, max_length=150)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0, le=100_000_000)
    sold_count: int | None = Field(default=None, alias="soldCount", ge=0, le=1_000_000_000)
    sales_label: str | None = Field(default=None, alias="salesLabel", max_length=150)
    minimum_order: str | None = Field(default=None, alias="minimumOrder", max_length=100)
    price_label: str | None = Field(default=None, alias="priceLabel", max_length=100)
    observed_at: str | None = Field(default=None, alias="observedAt")

    @field_validator("url")
    @classmethod
    def safe_store_url(cls, value):
        if value and (value.username or value.password):
            raise ValueError("Store URLs cannot include credentials.")
        return value


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    subtitle: str = Field(default="", max_length=250)
    brand: str = Field(default="Unknown", max_length=100)
    category: CategoryId
    art: Literal["headphones", "earbuds", "speaker", "watch", "phone", "laptop", "camera", "bag"] = "bag"
    tags: str = Field(default="", max_length=1000)
    price: float = Field(default=0, ge=0)
    old_price: float = Field(default=0, alias="oldPrice", ge=0)
    currency: CurrencyId = "PKR"
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0, le=100_000_000)
    sold_count: int | None = Field(default=None, alias="soldCount", ge=0, le=1_000_000_000)
    sales_label: str | None = Field(default=None, alias="salesLabel", max_length=150)
    image_url: HttpUrl | None = Field(default=None, alias="imageUrl")
    overview: list[str] = Field(default_factory=list, max_length=20)
    specifications: dict[str, str] = Field(default_factory=dict)
    comparison_price: float | None = Field(default=None, alias="comparisonPrice", ge=0)
    comparison_currency: str = Field(default="PKR", alias="comparisonCurrency")
    price_basis: str = Field(default="listed price", alias="priceBasis")
    shipping: bool = False
    offers: list[Offer] = Field(min_length=1, max_length=20)
    source: Literal["demo", "live"] = "demo"
    score: int = Field(default=0, ge=0, le=100)
    badge: str = Field(default="Recommended", max_length=60)
    recommendation: dict = Field(default_factory=dict)


class CompareRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=4)


class HistoryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=120)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value):
        value = " ".join(value.strip().lower().split())
        if not value:
            raise ValueError("Search cannot be blank.")
        return value


class SearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    q: str = Field(min_length=1, max_length=120)
    store: list[StoreId] | None = Field(default=None, max_length=7)
    category: CategoryId | None = None
    max_price: float | None = Field(default=None, alias="maxPrice", ge=0, le=100_000_000)
    free_shipping: bool = Field(default=False, alias="freeShipping")
    min_rating: float | None = Field(default=None, alias="minRating", ge=0, le=5)
    min_sales: int | None = Field(default=None, alias="minSales", ge=0, le=1_000_000_000)
    sort: Literal["recommended", "best-selling", "price-low", "price-high", "rating", "sales"] = "recommended"

    @field_validator("q")
    @classmethod
    def trim_query(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a product to search.")
        return value
