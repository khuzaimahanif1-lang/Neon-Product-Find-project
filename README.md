# NeonFind

A dark neon product-search application with a Python FastAPI backend and live WebSocket progress.

Discovery starts empty. After you search, the backend fetches marketplace listings, remembers discovered products in SQLite, applies filters, ranks the results and streams them to the UI. Buy buttons open the original marketplace listing.

## Run

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "./backend[dev]"
backend/.venv/Scripts/python.exe backend/run.py
```

The environment is already installed on this machine; subsequent runs need only the last command.

- Application: http://localhost:8001/#login
- API docs: http://localhost:8001/docs
- Health: http://localhost:8001/api/health
- Search socket: ws://localhost:8001/api/ws/search

Log in or browse as a guest, then search `headphones`. Products appear as sources respond. Daraz, Amazon, AliExpress and the Audionic Shopify storefront are enabled alongside Alibaba's structured-data adapter. The UI defaults to 4+ stars and 100+ published sales, with a combined sales-and-rating ranking and adjustable minimums. Restricted sources show their actual availability status; they need permitted API/data-service access. No sample products are silently used as live results.

Real accounts, revocable cookie sessions, saved finds, search history, offer details, product comparisons and explainable rankings are implemented. Ranking includes relevance, ratings, price value, reviews, published sales and delivery. Unknown fields remain unknown. Prices retain their displayed currency; converted comparisons are approximate.

FastAPI serves the frontend directly. The optional separate frontend (`npm run dev` in `frontend`, http://localhost:5173) connects to the same backend.

See [backend setup](backend/README.md), [live-search design and configuration](docs/LIVE_SEARCH.md), and [verification](docs/VERIFICATION.md). The [senior presentation prompt](docs/SLIDES_PROMPT.md) can be copied into a slide-generation tool.
