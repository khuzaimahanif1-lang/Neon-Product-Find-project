# NeonFind architecture

```mermaid
flowchart LR
    UI[Dark neon frontend] -->|Query and filters over WebSocket| WS[FastAPI search stream]
    WS --> Service[Catalog coordinator]
    Service --> Cache[Bounded query and source TTL caches]
    Service --> Public[Concurrent HTTPX public adapters]
    Service -. optional configured sources .-> APIs[Permitted API adapters]
    Public --> Normalize[Validate original listings and metrics]
    APIs --> Normalize
    Normalize --> DB[(Async SQLAlchemy / SQLite)]
    Service --> Ranking[RapidFuzz and weighted ranking]
    Ranking -->|Source updates and final results| WS
    WS --> UI
    UI -->|Account, offers and comparison REST| REST[FastAPI HTTP routes]
    REST --> Auth[Argon2 and revocable JWT cookie sessions]
    Auth --> DB
    REST --> DB
    UI -->|Buy link chosen by user| Store[Original marketplace listing and checkout]
```

Discovery starts empty. The backend fetches and persists source records before emitting product updates. Public access restrictions remain source errors; healthy sources are retained. Checkout is external to NeonFind. Ranking/filtering/currency conversion occur on the server; the UI renders the response.

```mermaid
erDiagram
    USERS ||--o{ AUTH_SESSIONS : has
    USERS ||--o{ SAVED_FINDS : saves
    CATALOG_PRODUCTS ||--o{ SAVED_FINDS : references
    USERS ||--o{ SEARCH_HISTORY : records
    USERS {
        string id PK
        string email UK
        string password_hash
    }
    AUTH_SESSIONS {
        string id PK
        string user_id FK
        datetime expires_at
    }
    CATALOG_PRODUCTS {
        string id PK
        string source
        json payload
        datetime updated_at
    }
    SAVED_FINDS {
        string user_id PK,FK
        string product_id PK,FK
        datetime created_at
    }
    SEARCH_HISTORY {
        int id PK
        string user_id FK
        string query
        datetime searched_at
    }
```

Offers, source metrics and overview information are embedded in the catalog JSON payload. Search uniqueness is scoped to user/query. Saved finds reference stable discovered listing IDs. Public adapters keep separate listing identities, while configured API adapters may provide verified canonical identity mappings. See [live-search details](LIVE_SEARCH.md).

## Source expansion and quality eligibility

Daraz, Amazon, AliExpress and Audionic Shopify are default public sources; Alibaba reports access restrictions when needed. Configured Shopify shops have independent source IDs, provider caches and progress entries. Public Shopify predictive search is followed by bounded HTML lookups for overview and product-specific review widgets/JSON-LD; optional card/badge selectors require an exact product link before accepting a published sales label.

minRating and minSales are validated backend filters. Eligibility applies to source offers before choosing the Buy link; unknown metrics fail active minima. The UI defaults to 4 stars/100 sales and best-selling ranking (relevance 25%, rating 30%, sales 30%, reviews 10%, price value 5%). Sales groups for Shopify are separated by domain. Checkout stays on the original merchant website.
