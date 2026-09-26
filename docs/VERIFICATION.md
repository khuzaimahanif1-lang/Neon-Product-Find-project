# NeonFind live-search verification

Verified locally on September 12, 2026, on Windows with Python **3.14.6**. This records a local development application and observed source responses, not a public deployment or a guarantee of future marketplace availability.

## Automated checks

| Check | Observed result |
| --- | --- |
| Backend pytest suite | **70 passed**, 2 dependency deprecation warnings, 20.81 seconds on the final run |
| Ruff lint | All checks passed |
| Ruff formatting check | **19 source/test Python files** already formatted |
| pip check | No broken requirements |
| Frontend JavaScript syntax checks | Passed |
| Frontend build | Passed; static output generated in frontend/dist |

The warnings concern Starlette HTTPX TestClient compatibility and an AnyIO BlockingPortal alias. They did not fail the tests. Suite runtime is not an API latency or throughput measurement. The resolved dependency set is in [requirements-lock.txt](../backend/requirements-lock.txt).

## Behavior exercised by tests

- Account registration/login, duplicate and invalid credentials, private endpoint access, omitted credential values in validation errors, cookie attributes, logout and rejection of a replayed revoked session.
- Database persistence, user ownership isolation, idempotent saves and eight distinct recent queries per account.
- Fuzzy/alias matching, unmatched queries, backend store/category/free-delivery/budget filtering, pagination, ranking and sorting.
- Comparison bounds/distinct IDs, eligible offers, recommendation components, cache/filter isolation and canonical identity conflicts.
- HTTP origin guards, authentication rate limiting and production-secret requirements.
- Simulated provider retries, invalid source URLs, concurrency, partial failures and all-source errors.
- Empty search returning zero products without external requests; per-source updates before slower sources finish; persistence before an update is emitted; no fixture substitution when live sources fail.
- WebSocket origin validation, empty/invalid/rate-limited requests and cancellation when a request is replaced.
- Daraz published sales/ratings/description parsing, Amazon fractional USD and localized PKR prices, monthly sales labels and Alibaba access challenges.
- Unknown delivery remaining unknown, conversion-aware budget eligibility and verified-base exchange-rate inversion.

Provider/protocol tests use injected adapters or HTTPX MockTransport. They are separate from actual source access below. The checked-in eight-product demo catalog remains an explicit development/test option, not the default live catalog.

## Sales/rating and expanded-source verification

The search UI now defaults to **4+ stars**, **100+ published sales**, and **Sales & rating** ranking. Active minima exclude missing metrics. Rating/sales eligibility is checked on source offers before selecting purchase links. Tests cover boundary values, missing metrics, offer-specific ratings, sales/rating priority over cheap price, REST/WebSocket consistency and filter isolation across cached searches.

A verified backend run returned **37 qualifying listings**: Daraz 13, Amazon 6, AliExpress 11 and Audionic Shopify 7. Every returned product met both minimums. Source responses contained 24 Daraz, 7 Amazon, 24 AliExpress and 10 Shopify raw listings; fuzzy relevance and metric eligibility narrow these before display. Alibaba remained restricted. A later browser run displayed 47 qualifying finds; the subsequent capture showed 43 after source data changed. Counts/values vary between requests.

A stricter REST search with minRating=4.8 and minSales=1000 returned 9 matches, all meeting both limits. Browser controls were exercised for these values and displayed eligible listings. Discovery remained empty before the first search.

Audionic's public Shopify predictive endpoint and product pages were verified. The Hammer Wireless Headphone record contained rating **4.78**, **468 reviews**, a **5k Sold** label and listed price **PKR 6,999**. The UI displayed its source overview, unknown delivery and an original audionic.co product link. Review widgets matched the native product ID; sales badges came from a card linking to the same product. Tests reject metrics from unrelated products and cover the HTML-vs-JSON detail-response negotiation.

Multiple Shopify storefronts have independent source IDs, progress entries and cache keys. New tests cover decimal native currencies, unsafe/local URLs, private DNS addresses, foreign product links, detail failures, later matching review JSON-LD and product-specific public widget/card metrics. No Shopify customer/order/admin data was accessed.

New upgrade captures: [Quality filters and sources](assets/quality-results.png), [Rating/sales controls](assets/quality-controls.png), [Shopify overview and Buy link](assets/shopify-offers.png). Earlier captures below document previous live runs and may show the earlier ranking UI.

## Earlier live-source observations

The running FastAPI application at http://localhost:8001/ fetched public source pages through the backend. A headphones search returned **31 usable listings: 24 from Daraz and 7 from Amazon**, confirmed through the streamed browser results and the backend REST search response. Alibaba reported restricted/unsupported public access; no challenge was bypassed and no substitute products were shown.

The Amazon page displayed PKR, including a listing at PKR 5,546.17. The adapter preserved that currency and fractional price. Daraz results contained published sales labels, sellers, ratings, reviews and description bullets. The exchange-rate service returned source/update metadata; currency conversion remains approximate.

A later browser search returned **24 Daraz listings while Amazon timed out**. Healthy results stayed visible and the UI showed separate Amazon and Alibaba errors. Source access, listing order and values can change between requests; these counts describe particular observed runs.

No official marketplace API credentials were configured. Public-page fetching needs outbound backend network access. The HTTPX client uses native system certificate trust with certificate and hostname verification retained.

## Browser checks

1. Opened discovery with **zero product cards before searching**.
2. Submitted headphones and observed per-source progress followed by live product cards; no frontend request to a marketplace was needed.
3. Selected a Daraz listing and an Amazon listing and opened the backend comparison. The table displayed native prices, published/unknown totals, estimates, ratings, reviews, sales labels, sellers and delivery information.
4. Verified original comparison Buy links: https://www.daraz.pk/products/p47-i666091510.html and https://www.amazon.com/dp/B0HBWW3958. The links were inspected without purchasing or initiating checkout.
5. Opened another Daraz listing and verified **14 published overview bullets**, seller information, reviews/sales and an unknown-delivery label. Its Buy link pointed to https://www.daraz.pk/products/p47-i220638343.html.
6. Confirmed a later partial search retained healthy Daraz cards when Amazon timed out.

Earlier MVP browser checks exercised signup/login, account save/reload/history, credentialed frontend-to-backend requests and logout using sample fixtures. Those sample journeys are historical; account/persistence tests passed again in the current 70-test suite. No credentials are included in this record.

## Screenshots for the senior presentation

Current captures from the upgraded application:

- [Empty discovery](assets/empty-discovery.png)
- [Live results](assets/live-results.png)
- [Published listing overview](assets/live-offers.png)
- [Daraz and Amazon comparison](assets/live-comparison.png)

These are observations from the local run, not guaranteed current offers. Older login.png, dashboard.png, offers.png and comparison.png files show the previous sample-catalog UI and must not be presented as live marketplace prices.

The OpenAPI schema has **18 HTTP operations**; the WebSocket endpoint is documented separately in [LIVE_SEARCH.md](LIVE_SEARCH.md).

## Practical limits

Alibaba requires permitted API/data-source access when its public pages are restricted; implemented structured-data support does not guarantee reachable listings. Amazon/Daraz public adapters can also be restricted or change. Missing sales figures, descriptions, seller names and delivery fees remain unknown. Sales labels have different reporting periods. Similar listings can represent different variants or quantities, and checkout happens on the original store.

Email recovery, alerts, database migrations/PostgreSQL, shared Redis limits/caches, trained models, public deployment and production operational testing remain future work. No load test, security certification, coverage percentage, revenue result or business-impact study is claimed.
