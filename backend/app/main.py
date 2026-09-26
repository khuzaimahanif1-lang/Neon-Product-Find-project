import ssl
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
import structlog
import truststore
from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import FRONTEND_DIR, STORE_NAMES, Settings
from .database import create_database, initialize_database
from .middleware import RequestPolicy
from .models import AuthSession, CatalogProduct, SavedFind, SearchHistory, User
from .providers import CatalogService, CatalogUnavailable
from .recommendations import rank_products
from .schemas import (
    CategoryId,
    CompareRequest,
    HistoryRequest,
    LoginRequest,
    Product,
    SignupRequest,
    StoreId,
    UserResponse,
)
from .security import (
    COOKIE_NAME,
    current_user,
    decode_session,
    dummy_password_hash,
    hash_password,
    issue_session,
    load_signing_secret,
    verify_password,
)
from .streaming import register_search_socket


async def get_session(request: Request):
    async with request.app.state.sessions() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def serialize(product):
    return product.model_dump(mode="json", by_alias=True)


async def lookup_product(identifier, session):
    row = await session.get(CatalogProduct, identifier)
    if row is None:
        raise HTTPException(404, "Product not found. Search for it first.")
    return Product.model_validate(row.payload)


def create_app(settings: Settings | None = None, provider_adapters=None):
    settings = settings or Settings()
    structlog.configure(
        processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
    )
    engine, sessions = create_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        app.state.signing_secret = load_signing_secret(settings)
        await initialize_database(engine, sessions, settings)
        async with httpx.AsyncClient(
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            timeout=httpx.Timeout(
                settings.provider_timeout_seconds, connect=min(10, settings.provider_timeout_seconds)
            ),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        ) as client:
            app.state.catalog_service = CatalogService(settings, sessions, client, adapters=provider_adapters)
            try:
                yield
            finally:
                await engine.dispose()

    app = FastAPI(
        title="NeonFind API",
        version="1.0.0",
        description="Server-side marketplace search, live WebSocket progress, explainable product ranking and persistent accounts. No catalog is returned until a product is searched.",
        lifespan=lifespan,
    )
    app.state.settings, app.state.sessions = settings, sessions
    app.state.websocket_counters = TTLCache(maxsize=10_000, ttl=60)
    register_search_socket(app)
    app.middleware("http")(RequestPolicy(settings))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, error):
        # FastAPI's default validation response includes input values; omit them for credential fields.
        return JSONResponse(
            {
                "detail": [
                    {"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()
                ]
            },
            status_code=422,
        )

    @app.exception_handler(CatalogUnavailable)
    async def unavailable(_, error):
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.get("/api/health", tags=["system"])
    async def health(session: Session):
        await session.execute(select(1))
        return {"status": "ok", "database": "ok", "catalogMode": settings.catalog_mode, "version": "1.0.0"}

    @app.get("/api/catalog/meta", tags=["system"])
    async def metadata(request: Request):
        return {
            "mode": settings.catalog_mode,
            "currency": settings.search_currency,
            "rankingMethod": "weighted-rules-v2",
            "rankingMethods": ["weighted-rules-v2", "weighted-sales-rating-v1"],
            "shopifyConfigured": any(
                provider.config.store == "shopify" for provider in request.app.state.catalog_service.providers
            ),
            "searchTransport": "websocket",
            "providers": list(request.app.state.catalog_service.status.values()),
            "sampleData": settings.catalog_mode == "demo",
            "authentication": "real",
            "stores": [{"id": key, "name": value} for key, value in STORE_NAMES.items()],
        }

    @app.get("/api/providers", tags=["system"])
    async def provider_status(request: Request):
        return {
            "mode": settings.catalog_mode,
            "providers": list(request.app.state.catalog_service.status.values()),
        }

    @app.post("/api/auth/signup", status_code=201, tags=["authentication"])
    async def signup(data: SignupRequest, request: Request, response: Response, session: Session):
        email = str(data.email).lower()
        if await session.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, "An account with this email already exists.")
        user = User(email=email, name=data.name, password_hash=await hash_password(data.password))
        session.add(user)
        try:
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(409, "An account with this email already exists.") from error
        return await issue_session(user, session, request, response)

    @app.post("/api/auth/login", tags=["authentication"])
    async def login(data: LoginRequest, request: Request, response: Response, session: Session):
        user = await session.scalar(select(User).where(User.email == str(data.email).lower()))
        valid = await verify_password(data.password, user.password_hash if user else dummy_password_hash)
        if not user or not valid:
            raise HTTPException(401, "Email or password is incorrect.")
        return await issue_session(user, session, request, response)

    @app.get("/api/auth/me", response_model=UserResponse, tags=["authentication"])
    async def me(request: Request, session: Session):
        return await current_user(request, session)

    @app.post("/api/auth/logout", tags=["authentication"])
    async def logout(request: Request, response: Response, session: Session):
        claims = decode_session(request)
        if claims:
            await session.execute(
                delete(AuthSession).where(
                    AuthSession.id == claims["jti"], AuthSession.user_id == claims["sub"]
                )
            )
            await session.commit()
        response.delete_cookie(
            COOKIE_NAME,
            path="/",
            httponly=True,
            samesite="strict",
            secure=settings.environment == "production",
        )
        return {"message": "Logged out."}

    @app.get("/api/products", tags=["catalog"])
    @app.get("/api/recommendations", tags=["catalog"])
    async def search(
        request: Request,
        q: str = Query(default="", max_length=120),
        store: Annotated[list[StoreId] | None, Query()] = None,
        category: CategoryId | None = None,
        max_price: Annotated[float | None, Query(alias="maxPrice", ge=0, le=100_000_000)] = None,
        free_shipping: Annotated[bool, Query(alias="freeShipping")] = False,
        min_rating: Annotated[float | None, Query(alias="minRating", ge=0, le=5)] = None,
        min_sales: Annotated[int | None, Query(alias="minSales", ge=0, le=1_000_000_000)] = None,
        sort: Literal[
            "recommended", "best-selling", "price-low", "price-high", "rating", "sales"
        ] = "recommended",
        limit: int = Query(default=100, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10_000),
    ):
        products, providers, hit = await request.app.state.catalog_service.catalog(q)
        ranked = rank_products(
            products,
            q,
            stores=store,
            max_price=max_price,
            free_shipping=free_shipping,
            min_rating=min_rating,
            min_sales=min_sales,
            category=category,
            sort=sort,
            rates=request.app.state.catalog_service.currency.rates,
            base_currency=settings.search_currency,
        )
        return {
            "query": q.strip(),
            "products": [serialize(product) for product in ranked[offset : offset + limit]],
            "total": len(ranked),
            "mode": settings.catalog_mode,
            "providers": providers,
            "partial": any(provider["status"] == "error" for provider in providers),
            "cache": {"hit": hit, "ttlSeconds": settings.cache_ttl_seconds},
            "exchangeRates": request.app.state.catalog_service.currency.metadata,
        }

    @app.post("/api/products/compare", tags=["catalog"])
    async def compare(data: CompareRequest, request: Request, session: Session):
        if len(set(data.ids)) != len(data.ids):
            raise HTTPException(422, "Choose distinct products for comparison.")
        products = [await lookup_product(identifier, session) for identifier in data.ids]
        ranked = {
            product.id: product
            for product in rank_products(
                products,
                rates=request.app.state.catalog_service.currency.rates,
                base_currency=settings.search_currency,
            )
        }
        ordered = [ranked[identifier] for identifier in data.ids]
        return {
            "products": [serialize(product) for product in ordered],
            "lowestTotalPrice": min(product.offers[0].total_price for product in ordered)
            if len({p.currency for p in ordered}) == 1
            and all(p.offers[0].total_price is not None for p in ordered)
            else None,
            "lowestComparisonPrice": min(
                (p.comparison_price for p in ordered if p.comparison_price is not None), default=None
            ),
            "mode": settings.catalog_mode,
        }

    @app.get("/api/products/{identifier}/offers", tags=["catalog"])
    async def offers(identifier: str, request: Request, session: Session):
        product = rank_products(
            [await lookup_product(identifier, session)],
            rates=request.app.state.catalog_service.currency.rates,
            base_currency=settings.search_currency,
        )[0]
        return {
            "productId": identifier,
            "offers": [offer.model_dump(mode="json", by_alias=True) for offer in product.offers],
            "source": product.source,
        }

    @app.get("/api/products/{identifier}", tags=["catalog"])
    async def product_details(identifier: str, request: Request, session: Session):
        return serialize(
            rank_products(
                [await lookup_product(identifier, session)],
                rates=request.app.state.catalog_service.currency.rates,
                base_currency=settings.search_currency,
            )[0]
        )

    @app.get("/api/me/saved", tags=["account"])
    async def saved(request: Request, session: Session):
        user = await current_user(request, session)
        rows = (
            await session.scalars(
                select(CatalogProduct)
                .join(SavedFind, SavedFind.product_id == CatalogProduct.id)
                .where(SavedFind.user_id == user.id)
                .order_by(SavedFind.created_at.desc())
            )
        ).all()
        return {
            "products": [
                serialize(product)
                for product in rank_products(
                    [Product.model_validate(row.payload) for row in rows],
                    rates=request.app.state.catalog_service.currency.rates,
                    base_currency=settings.search_currency,
                )
            ]
        }

    @app.put("/api/me/saved/{identifier}", tags=["account"])
    async def save(identifier: str, request: Request, session: Session):
        user = await current_user(request, session)
        await lookup_product(identifier, session)
        await session.execute(
            insert(SavedFind)
            .values(user_id=user.id, product_id=identifier)
            .on_conflict_do_nothing(index_elements=["user_id", "product_id"])
        )
        await session.commit()
        return {"productId": identifier, "saved": True}

    @app.delete("/api/me/saved/{identifier}", tags=["account"])
    async def unsave(identifier: str, request: Request, session: Session):
        user = await current_user(request, session)
        await session.execute(
            delete(SavedFind).where(SavedFind.user_id == user.id, SavedFind.product_id == identifier)
        )
        await session.commit()
        return {"productId": identifier, "saved": False}

    @app.get("/api/me/searches", tags=["account"])
    async def recent(request: Request, session: Session):
        user = await current_user(request, session)
        queries = (
            await session.scalars(
                select(SearchHistory.query)
                .where(SearchHistory.user_id == user.id)
                .order_by(SearchHistory.searched_at.desc())
                .limit(8)
            )
        ).all()
        return {"queries": list(queries)}

    @app.post("/api/me/searches", tags=["account"])
    async def record_search(data: HistoryRequest, request: Request, session: Session):
        user = await current_user(request, session)
        await session.execute(
            insert(SearchHistory)
            .values(user_id=user.id, query=data.query, searched_at=datetime.now(UTC))
            .on_conflict_do_update(
                index_elements=["user_id", "query"], set_={"searched_at": datetime.now(UTC)}
            )
        )
        old_ids = (
            await session.scalars(
                select(SearchHistory.id)
                .where(SearchHistory.user_id == user.id)
                .order_by(SearchHistory.searched_at.desc(), SearchHistory.id.desc())
                .offset(8)
            )
        ).all()
        if old_ids:
            await session.execute(delete(SearchHistory).where(SearchHistory.id.in_(old_ids)))
        await session.commit()
        return {"query": data.query, "recorded": True}

    @app.delete("/api/me/searches", tags=["account"])
    async def clear_recent(request: Request, session: Session):
        user = await current_user(request, session)
        await session.execute(delete(SearchHistory).where(SearchHistory.user_id == user.id))
        await session.commit()
        return {"message": "Search history cleared."}

    if FRONTEND_DIR.exists():
        app.mount("/src", StaticFiles(directory=FRONTEND_DIR / "src"), name="frontend-source")
        app.mount("/public", StaticFiles(directory=FRONTEND_DIR / "public"), name="frontend-public")

        @app.get("/", include_in_schema=False)
        async def frontend():
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
