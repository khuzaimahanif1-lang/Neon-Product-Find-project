import math
import re
from statistics import median

from rapidfuzz import fuzz

from .schemas import Product

WEIGHTS = {"relevance": 0.35, "rating": 0.20, "value": 0.20, "reviews": 0.10, "sales": 0.10, "delivery": 0.05}
BEST_SELLING_WEIGHTS = {
    "relevance": 0.25,
    "rating": 0.30,
    "value": 0.05,
    "reviews": 0.10,
    "sales": 0.30,
    "delivery": 0.0,
}


def sales_group(offer):
    return (offer.store, offer.url.host if offer.store == "shopify" and offer.url else "")


def normalize_query(query: str):
    text = re.sub(r"[^a-z0-9\s-]", " ", query.lower())
    text = re.sub(r"smart\s+watch", "smartwatch", text)
    text = re.sub(r"air\s+buds", "earbuds", text)
    aliases = {"airbuds": "earbuds", "earphones": "earbuds", "mobile": "phone", "notebook": "laptop"}
    return " ".join(aliases.get(word, word) for word in text.split())


def relevance(product: Product, query: str):
    if not query:
        return 1.0
    text = normalize_query(f"{product.name} {product.brand} {product.subtitle} {product.tags}")
    words = query.split()
    if all(word in text for word in words):
        return 1.0
    tokens = text.split()
    # Every search word must match, so one broad token cannot hide a missing model number.
    scores = [max((fuzz.ratio(word, token) for token in tokens), default=0) for word in words]
    if min(scores, default=0) < 70:
        return 0.0
    return sum(scores) / len(scores) / 100


def rank_products(
    catalog: list[Product],
    query="",
    stores=None,
    max_price=None,
    free_shipping=False,
    category=None,
    sort="recommended",
    rates=None,
    base_currency="PKR",
    min_rating=None,
    min_sales=None,
):
    rates = {**(rates or {}), base_currency: 1.0}
    normalized = normalize_query(query)
    candidates = []
    weights = BEST_SELLING_WEIGHTS if sort == "best-selling" else WEIGHTS

    def offer_key(offer):
        amount = offer.total_price if offer.total_price is not None else offer.price
        rate = rates.get(offer.currency)
        return (0, base_currency, amount * rate) if rate else (1, offer.currency, amount)

    for original in catalog:
        product = original.model_copy(deep=True)
        if category and product.category != category:
            continue
        product.offers = [
            offer
            for offer in product.offers
            if (not stores or offer.store in stores) and (not free_shipping or offer.shipping_fee == 0)
        ]

        def eligible_metrics(offer, original=original):
            rating = offer.rating
            if rating is None and all(item.rating is None for item in original.offers):
                rating = original.rating
            sold = offer.sold_count
            if sold is None and len(original.offers) == 1:
                sold = original.sold_count
            return (not min_rating or rating is not None and rating >= min_rating) and (
                not min_sales or sold is not None and sold >= min_sales
            )

        product.offers = [offer for offer in product.offers if eligible_metrics(offer)]
        if not product.offers:
            continue
        for offer in product.offers:
            offer.total_price = (
                round(offer.price + offer.shipping_fee, 2) if offer.shipping_fee is not None else None
            )
            offer.shipping = (
                "Delivery cost confirmed at checkout"
                if offer.shipping_fee is None
                else "Free delivery"
                if offer.shipping_fee == 0
                else f"{offer.currency} {offer.shipping_fee:,.2f} delivery"
            )
        product.offers.sort(key=offer_key)
        best = product.offers[0]
        for metric in ("rating", "reviews", "sold_count", "sales_label"):
            value = getattr(best, metric)
            if value is None and all(getattr(item, metric) is None for item in original.offers):
                if metric in {"rating", "reviews"} or len(original.offers) == 1:
                    value = getattr(original, metric)
            setattr(product, metric, value)
        if len(original.offers) == 1 and best.sold_count is None:
            best.sold_count, best.sales_label = product.sold_count, product.sales_label
        product.old_price = (
            max(product.old_price, best.price) if product.currency == best.currency else best.price
        )
        product.price, product.currency = best.price, best.currency
        product.shipping = best.shipping_fee == 0
        product.comparison_currency = base_currency
        amount = best.total_price if best.total_price is not None else best.price
        product.comparison_price = round(amount * rates[best.currency], 2) if best.currency in rates else None
        product.price_basis = (
            "including published delivery"
            if best.total_price is not None
            else "listed price; delivery unknown"
        )
        if max_price is not None and (
            product.comparison_price is None or product.comparison_price > max_price
        ):
            continue
        match = relevance(product, normalized)
        if match:
            candidates.append((product, match))
    category_prices, store_sales = {}, {}
    for product, _ in candidates:
        key = (product.category, base_currency if product.comparison_price is not None else product.currency)
        category_prices.setdefault(key, []).append(product.comparison_price or product.price)
        for offer in product.offers:
            if offer.sold_count is not None:
                group = sales_group(offer)
                store_sales[group] = max(store_sales.get(group, 0), offer.sold_count)
    for product, match in candidates:
        reviews = product.reviews or 0
        rating = product.rating
        adjusted_rating = ((rating * reviews + 4.0 * 50) / (reviews + 50) / 5) if rating is not None else 0.5
        key = (product.category, base_currency if product.comparison_price is not None else product.currency)
        value = min(1.0, median(category_prices[key]) / max(product.comparison_price or product.price, 0.01))
        sales = max(
            (
                math.log1p(offer.sold_count) / max(math.log1p(store_sales[sales_group(offer)]), 1)
                for offer in product.offers
                if offer.sold_count is not None
            ),
            default=0,
        )
        components = {
            "relevance": match,
            "rating": adjusted_rating,
            "value": value,
            "reviews": min(1.0, math.log1p(reviews) / math.log1p(2000)),
            "sales": sales,
            "delivery": float(product.shipping),
        }
        product.score = round(sum(components[key] * weight for key, weight in weights.items()) * 100)
        product.badge = "Strong match" if normalized else "Recommended"
        reasons = [
            f"{'Exact terms' if match == 1 else 'Fuzzy terms'} match the search.",
            f"Rating adjusted for {reviews:,} published reviews."
            if rating is not None
            else "Rating not published; neutral rating contribution.",
            "Price compared in the same currency group; exchange conversions are approximate.",
            "Published sales compared relative to listings from the same store; reporting periods can differ."
            if any(offer.sold_count is not None for offer in product.offers)
            else "Sales count not published.",
            "Published delivery included."
            if product.offers[0].total_price is not None
            else "Delivery is unknown; price is an estimate before delivery.",
        ]
        product.recommendation = {
            "method": "weighted-sales-rating-v1" if sort == "best-selling" else "weighted-rules-v2",
            "weights": weights,
            "components": {key: round(value, 4) for key, value in components.items()},
            "reasons": reasons,
            "disclaimer": "A rule-based ranking, not a probability. Confirm variant, quantity, stock and checkout total on the original store.",
        }
    result = [product for product, _ in candidates]
    if sort in {"price-low", "price-high"}:
        result.sort(key=lambda product: offer_key(product.offers[0]), reverse=sort == "price-high")
    elif sort == "rating":
        result.sort(
            key=lambda product: (product.rating if product.rating is not None else -1, product.reviews or 0),
            reverse=True,
        )
    elif sort == "sales":
        result.sort(
            key=lambda product: (product.recommendation["components"]["sales"], product.score), reverse=True
        )
    else:
        result.sort(key=lambda product: (product.score, product.reviews or 0), reverse=True)
    if result and sort in {"recommended", "best-selling"}:
        result[0].badge = "Best sales & rating" if sort == "best-selling" else "Best available match"
    return result
