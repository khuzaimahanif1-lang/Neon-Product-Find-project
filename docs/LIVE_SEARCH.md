# NeonFind backend marketplace search

Discovery starts with no products. A search opens a WebSocket to FastAPI. The backend fetches store listings, validates and remembers them in SQLite, filters eligible offers, ranks products and streams updates. The frontend renders the server results. Buy buttons open the original marketplace listing; checkout takes place there.

## Sources

Daraz's public catalog JSON adapter extracts published prices, original prices, ratings, reviews, sold-count labels, sellers, images, description bullets and item URLs. Amazon's public HTML adapter extracts priced search listings, their actual displayed currency, ratings/reviews, available monthly-purchase labels, images and ASIN links. Listings with no usable published price are omitted. Alibaba's structured-data adapter parses Product JSON-LD when exposed; an access challenge or unsupported page produces a source error. AliExpress is enabled by default and parses its public embedded search data (including ratings, sold labels and the displayed currency), with Product JSON-LD as a fallback. Audionic is a verified default Shopify storefront; additional shops are configurable. Shopify uses public predictive search, then bounded product-page lookups for description, product-ID-scoped review widgets/JSON-LD and, where configured, sales badges on cards linking to that exact product. Missing metrics remain unknown.

Live public access can vary or break when a store changes its page. Healthy sources remain visible during partial failure; an all-source failure is reported as an error. No access challenge is bypassed and fixtures are never substituted for live data. Enabled `providers.json` adapters replace their store's public adapter with a permitted normalized API/data-service source. API-specific signing and mapping need an appropriate implementation; generic Bearer authorization is not an official marketplace integration.

## Configuration

Use `backend/.env.example`. Live mode is the default. Demo fixtures are used only if `NEONFIND_CATALOG_MODE=demo` is explicitly selected, and only after a search.

```dotenv
NEONFIND_CATALOG_MODE=live
NEONFIND_PUBLIC_MARKETPLACES=["daraz","amazon","alibaba"]
NEONFIND_MARKETPLACE_LIMIT=24
NEONFIND_PROVIDER_TIMEOUT_SECONDS=20
NEONFIND_FETCH_EXCHANGE_RATES=true
NEONFIND_SEARCH_CURRENCY=PKR
```

Set both public marketplaces and Shopify stores to `[]` for API-only operation. `JsonApiProvider` expects `{"products": [...]}` in the backend Product schema. Keys remain in backend environment variables or `.env`. See [Amazon Creators API](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction) for Amazon's current official API path. Seller APIs must not be assumed to expose unrestricted whole-marketplace search.

## WebSocket protocol

Connect to `ws://localhost:8001/api/ws/search` with an allowed browser Origin (`wss://` behind HTTPS). Send:

```json
{"requestId":"search-1","q":"headphones","store":null,"category":null,"maxPrice":null,"freeShipping":false,"minRating":4,"minSales":100,"sort":"best-selling"}
```

Events: `started` with no products → one `update` per finished source → `complete`. Each includes the request ID, ranked products, provider statuses, count, data mode, partial status and exchange-rate metadata. An `error` frame includes an actionable message. A replacement request or disconnect cancels outstanding work. The UI cancels previous searches and closes a completed search connection.

All filters and sorts run on the server. Sorts: best-selling, recommended, price-low, price-high, rating, sales. The UI defaults to 4+ stars, 100+ published sales and best-selling; REST/WebSocket clients can set minRating/minSales independently or omit them for all matches. A zero/null minimum disables that requirement. Missing metrics never pass an active minimum. Filters apply to source offers before the eligible purchase link is selected. The backend constrains origins, request size (4 KB), process-local request rates, provider response size, per-source deadlines and concurrent fetching. REST search uses the same catalog and ranking services. Empty discovery queries return zero products and make no store request.

## Ranking and prices

Sales & rating mode weights: relevance **25%**, Bayesian rating **30%**, published sales **30%**, review volume **10%**, price value **5%**. It emphasizes sales and rating rather than the cheapest price. Recommended mode weights: relevance **35%**, Bayesian rating **20%**, category/currency price value **20%**, review volume **10%**, published sales **10%**, free delivery **5%**. Relevance uses fuzzy title/overview matching. Sales are normalized relative to other listings from their own store because reporting periods differ. Separate Shopify domains have separate sales groups. Source labels such as `5.8K sold` and `10K+ bought in past month` remain visible; parsed counts retain their rounded/lower-bound meaning. Missing data remains null and appears in the explanation. This is a rule-based ranking, not a trained model or a confidence probability.

Native currencies and fractional prices are preserved, including Amazon's localized PKR prices. Published delivery produces `totalPrice`; unknown delivery leaves the total null. `comparisonPrice` converts the known total or, when shipping is unknown, the listed price to the comparison currency. `priceBasis` identifies this distinction. Unconverted currencies are grouped separately in price sorting and excluded from base-currency budget filters. Unknown delivery never qualifies as free shipping.

Approximate conversions use the [ExchangeRate-API open endpoint](https://www.exchangerate-api.com/docs/free), a one-hour cache and visible attribution. Only converted prices and source/update metadata are returned. Automatic fetching can be disabled and `NEONFIND_CURRENCY_RATES` configured as base units per foreign unit. Confirm variant, quantity, taxes, delivery and final checkout price on the original site.

Separate public listing IDs avoid claiming similarly named products have identical variants. API authors can assign a shared canonical ID only for verified matching brand/model/variant; conflicts in normalized name and brand are rejected.

## Backend memory

Successful source results and healthy complete searches have bounded 60-second caches. Each Shopify storefront has its own source ID, progress status and provider-cache key. Partial searches reuse healthy source caches and retry unavailable sources. Discovered products are upserted into SQLite before their stream update is sent, allowing immediate saving, details and comparison. Account saves/history remain private per user. Guest lists are separate browser-local data. Saved products are an independent view and can appear there before a new discovery search.

## Implementation and verification

`marketplaces.py` parses public data with Beautiful Soup/lxml and handles rates. `providers.py` coordinates source fetching, caching and persistence. `recommendations.py` filters/ranks. `streaming.py` handles the live protocol. The HTTPX client retains certificate and hostname verification through native system trust, using the [HTTPX SSL configuration](https://www.python-httpx.org/advanced/ssl/). The protocol uses [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/).

See [verification results](VERIFICATION.md) for automated and real-browser checks. Public source availability observations apply to that run, not a guarantee of future access. Email recovery, price alerts, PostgreSQL/migrations, distributed cache/rate limits and public deployment remain future work.

## Adding Shopify stores

Shopify is a platform for individual shops, so each shop needs its public HTTPS URL and displayed currency in backend settings. Audionic (audionic.co, PKR) is enabled by default. Edit backend/.env to replace or expand the list, then restart FastAPI:

```dotenv
NEONFIND_SHOPIFY_STORES=[{"name":"Audionic","url":"https://audionic.co","currency":"PKR","product_card_selector":".product-item","sales_badge_selector":".feature-badge"},{"name":"Your shop","url":"https://your-shop.myshopify.com","currency":"USD"}]
```

Replace the example shop URL before enabling it. Up to eight storefronts are supported, with up to ten predictive matches per shop. Locale paths are supported. product_card_selector and sales_badge_selector are optional store-specific CSS selectors: labels are accepted only from a card containing this product's exact path. Sales totals are not generally part of [Shopify predictive search](https://shopify.dev/docs/api/ajax/reference/predictive-search); if no trustworthy public label is found, the sales count remains null. Review widgets must match the native product ID. Product-page requests explicitly request HTML; predictive search requests JSON. Detail failures retain usable listings with unknown metrics, which do not meet active filters.

No Shopify Admin/customer/order endpoints are accessed. URLs are backend configuration, HTTPS and domain constrained, with local/private address checks. Public source access remains variable. A normalized Shopify API/data-service adapter may specify allowed_domains for its custom storefront domains; keys stay on the backend. PriceOye and Telemart still require their configured API/data-source adapters.
