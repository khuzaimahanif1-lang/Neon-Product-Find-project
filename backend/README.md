# NeonFind FastAPI backend

The backend handles marketplace fetching, normalization, caching, persistence, filtering, ranking and WebSocket progress. The discovery catalog is empty until a product is searched. `live` is the default; `demo` is an explicit development mode.

## Start

From the project root with Python 3.11+ (verified locally on Python 3.14.6):

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "./backend[dev]"
backend/.venv/Scripts/python.exe backend/run.py
```

Open http://localhost:8001/#login and http://localhost:8001/docs. Node is optional because FastAPI serves the frontend. The separate frontend on port 5173 uses explicit credentialed CORS origins. Port 8001 avoids the unrelated service already using port 8000.

## Libraries

| Library | Purpose |
| --- | --- |
| FastAPI / Uvicorn / websockets | Validated HTTP routes, OpenAPI and WebSocket search |
| Pydantic / Pydantic Settings / python-dotenv | Input/provider validation and environment configuration |
| SQLAlchemy / aiosqlite | Async SQLite persistence |
| pwdlib with Argon2 / PyJWT | Password hashing and expiring revocable cookie sessions |
| HTTPX / truststore | Concurrent HTTP with verified native system certificate trust |
| Beautiful Soup / lxml | Server-side HTML and structured-data parsing |
| Tenacity | Selected transient retries for configured API adapters |
| RapidFuzz | Typo-tolerant relevance matching |
| cachetools | Bounded catalog/provider caches and process-local rate counters |
| structlog | Sanitized structured request/provider logs |
| pytest / pytest-asyncio / Ruff | Behavior tests, asynchronous tests and code checks |

See the [HTTPX SSL documentation](https://www.python-httpx.org/advanced/ssl/) and [FastAPI WebSocket documentation](https://fastapi.tiangolo.com/advanced/websockets/) for the transport configuration.

## Product sources

Daraz public catalog JSON and Amazon public HTML parsers extract published search listing information. Amazon's actual displayed currency is preserved, including localized PKR prices. AliExpress parses its public embedded search data, preserving product ratings, sold labels and actual currency, with JSON-LD fallback. Alibaba parses Product JSON-LD when exposed. Audionic is enabled as a Shopify storefront, using public predictive search and bounded page lookups for product-specific descriptions, reviews and sales badges. Access challenges, restricted responses and unsupported pages produce a source error. No browser automation or access-challenge bypass is used.

Enabled `providers.json` entries replace a store's public adapter with `JsonApiProvider`. This expects `{"products":[...]}` in the normalized `Product` schema shown in `/openapi.json`. Store URLs must match that store's allowed domain. Credentials remain in the backend environment or `.env`. Official APIs may require custom signing, access permissions and field mapping; a generic Bearer header does not implement them.

See [live-search documentation](../docs/LIVE_SEARCH.md) for the full protocol, source behavior, price handling, ranking weights and extension points.

## Endpoints

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/health`, `/api/catalog/meta`, `/api/providers` | Health, configuration and sanitized source status |
| POST | `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout` | Create/login/revoke session |
| GET | `/api/auth/me` | Current user |
| GET | `/api/products`, `/api/recommendations` | Backend search/filter/rank; empty query returns no products |
| WebSocket | `/api/ws/search` | Started, source updates, final results/errors |
| GET | `/api/products/{id}`, `/api/products/{id}/offers` | Remembered product and source offers |
| POST | `/api/products/compare` | Compare 1–4 distinct discovered products |
| GET | `/api/me/saved` | Current user's saved products |
| PUT / DELETE | `/api/me/saved/{id}` | Save/remove a discovered product |
| GET / POST / DELETE | `/api/me/searches` | Eight distinct recent account searches |

REST search accepts `q`, repeated `store`, `category`, `maxPrice`, `freeShipping`, `minRating`, `minSales`, `sort`, `limit`, `offset`. WebSocket filters use the same names, with store as an array. Search transport and ranking run on the server. Saved products are an independent account view.

## Accounts and data

Argon2 hashes are stored instead of passwords. JWTs are in HttpOnly SameSite=Strict cookies, with Secure enabled in production configuration. Database session IDs allow logout revocation. Browser storage contains no session tokens. User-owned saves and history are isolated by account. Development signing keys persist in ignored `data/.jwt-secret`; production requires an explicit secret of at least 32 characters. Unsafe browser request origins and search rates are constrained.

SQLite has users, auth sessions, discovered catalog products with JSON offers, saved finds and search history. Live discoveries are stored before their streamed update, so saving/comparing is immediately available. Development fixtures are seeded only in explicit demo mode. Previously saved fixture records retain their source labels.

## Configuration

Copy `.env.example` to `.env` when customizing. Public marketplaces default to Daraz, Amazon, Alibaba and AliExpress. Shopify defaults to Audionic (PKR); replace/expand NEONFIND_SHOPIFY_STORES with individual public store URLs, currencies and optional product-card/sales-badge selectors. Set both `NEONFIND_PUBLIC_MARKETPLACES=[]` and `NEONFIND_SHOPIFY_STORES=[]` for API-only operation. `NEONFIND_MARKETPLACE_LIMIT` bounds listings per source. `NEONFIND_FETCH_EXCHANGE_RATES` controls approximate comparison conversion. API adapter entries and their environment key names go in `providers.json`; never place secrets in frontend config.

Successful source/search caches default to 60 seconds. Failed sources are retried on the next query while healthy cached source results are reused. Provider requests have deadlines and bounded response sizes; no periodic scraping happens without a search. All unavailable sources produce an error instead of a fixture fallback.

## Checks

From `backend`:

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app run.py tests
.venv/Scripts/python.exe -m ruff format --check app run.py tests
.venv/Scripts/python.exe -m pip check
```

Resolved dependencies are recorded in `requirements-lock.txt`. For reproduction, install the lock file in a fresh environment, then install this project with `pip install -e . --no-deps`.

Live marketplace access remains subject to actual source availability. Email recovery, price alerts, PostgreSQL/migrations, distributed Redis policies and public production deployment are future work. Use one local process for the current in-memory cache/rate policies. See [verification](../docs/VERIFICATION.md).

Search UI defaults to minimum rating 4 and sales 100. Active minima exclude missing metrics and select eligible source offers before ranking. The best-selling sort uses relevance 25%, Bayesian rating 30%, published sales 30%, reviews 10% and value 5%. Recommended mode keeps its original weights. Shopify shops have independent progress/cache identities and sales groups.
