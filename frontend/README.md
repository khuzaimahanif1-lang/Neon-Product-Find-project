# NeonFind frontend

Vanilla JavaScript, HTML and CSS with a dark neon theme. FastAPI serves this UI directly at http://localhost:8001/#login. Login/signup, guest browsing, source progress, offer details, comparison, saved finds and search history are connected to the backend.

The discovery screen starts empty. Searching creates a WebSocket request; the UI displays ranked source updates and final results. Fetching, filtering, price conversion, caching and ranking run on FastAPI. Buy links open the original store. Unknown fields are labeled instead of fabricated.

## Optional Node server

```powershell
cd frontend
npm run dev
```

Port 5173 connects to backend port 8001. `npm run check` validates source syntax, `npm run build` generates `dist`, and `npm run preview` serves port 4173 with the same local backend. For different domains/ports, set `NEONFIND_CONFIG.apiBase` in `public/config.js` and allow that frontend origin in the backend. Never put API secrets in this file.

Registered saves/history live in SQLite under the account. Guest lists use separate local IDs; remembered product details are restored from backend catalog records without populating discovery. Tokens and passwords are not stored in local storage. See [backend configuration](../backend/README.md).

Search defaults to 4+ stars, 100+ published sales and Sales & rating ranking. Minimum rating and sales are adjustable; Any disables a requirement and allows unknown metrics for that requirement. Daraz, Amazon, AliExpress and Audionic Shopify results are fetched through FastAPI; additional storefronts are configured in backend settings.
