import asyncio
import json
from time import monotonic

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .providers import CatalogUnavailable
from .recommendations import rank_products
from .schemas import SearchRequest


def register_search_socket(app):
    @app.websocket("/api/ws/search")
    async def search_socket(socket: WebSocket):
        settings = socket.app.state.settings
        if socket.headers.get("origin") not in settings.allowed_origins:
            await socket.close(code=1008)
            return
        await socket.accept()
        active = None
        counters = socket.app.state.websocket_counters
        key = socket.client.host if socket.client else "unknown"

        async def run_search(data, request_id):
            service = socket.app.state.catalog_service
            try:
                async for event in service.events(data.q):
                    products = rank_products(
                        event["products"],
                        data.q,
                        stores=data.store,
                        max_price=data.max_price,
                        free_shipping=data.free_shipping,
                        min_rating=data.min_rating,
                        min_sales=data.min_sales,
                        category=data.category,
                        sort=data.sort,
                        rates=service.currency.rates,
                        base_currency=settings.search_currency,
                    )
                    await socket.send_json(
                        {
                            **event,
                            "requestId": request_id,
                            "query": data.q,
                            "products": [p.model_dump(mode="json", by_alias=True) for p in products],
                            "total": len(products),
                            "mode": settings.catalog_mode,
                            "partial": any(p["status"] == "error" for p in event["providers"]),
                            "exchangeRates": service.currency.metadata,
                        }
                    )
            except CatalogUnavailable as error:
                await socket.send_json({"type": "error", "requestId": request_id, "message": str(error)})
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception:
                await socket.send_json(
                    {
                        "type": "error",
                        "requestId": request_id,
                        "message": "Search could not be completed. Please try again.",
                    }
                )

        try:
            while True:
                raw = await socket.receive_text()
                if len(raw) > 4096:
                    await socket.close(code=1009)
                    break
                if active:
                    active.cancel()
                    await asyncio.gather(active, return_exceptions=True)
                request_id = None
                try:
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("Expected an object.")
                    request_id = str(payload.pop("requestId", ""))[:64]
                    data = SearchRequest.model_validate(payload)
                except (ValueError, ValidationError):
                    await socket.send_json(
                        {
                            "type": "error",
                            "requestId": request_id,
                            "message": "Enter a product and valid search filters.",
                        }
                    )
                    continue
                now = monotonic()
                used, start = counters.get(key, (0, now))
                if now - start >= 60:
                    used, start = 0, now
                counters[key] = (used + 1, start)
                if used >= settings.websocket_limit_per_minute:
                    await socket.send_json(
                        {
                            "type": "error",
                            "requestId": request_id,
                            "message": "Too many live searches. Try again in a minute.",
                        }
                    )
                    continue
                active = asyncio.create_task(run_search(data, request_id))
        except WebSocketDisconnect:
            pass
        finally:
            if active:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
