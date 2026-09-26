import json

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import BACKEND_DIR, Settings
from .models import Base, CatalogProduct
from .schemas import Product


def create_database(settings: Settings):
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def initialize_database(engine, sessions, settings: Settings):
    (BACKEND_DIR / "data").mkdir(parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    if settings.catalog_mode == "demo":
        catalog = json.loads((BACKEND_DIR / "app" / "data" / "catalog.json").read_text(encoding="utf-8"))
        async with sessions() as session:
            for raw in catalog:
                raw["source"] = "demo"
                for offer in raw["offers"]:
                    offer["source"] = "demo"
                product = Product.model_validate(raw)
                existing = await session.get(CatalogProduct, product.id)
                if existing is None:
                    session.add(
                        CatalogProduct(
                            id=product.id,
                            name=product.name,
                            category=product.category,
                            source="demo",
                            payload=product.model_dump(mode="json", by_alias=True),
                        )
                    )
            await session.commit()


async def load_catalog(session, source: str):
    rows = (await session.scalars(select(CatalogProduct).where(CatalogProduct.source == source))).all()
    return [Product.model_validate(row.payload) for row in rows]
