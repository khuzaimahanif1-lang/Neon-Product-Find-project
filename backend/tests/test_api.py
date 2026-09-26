import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.security import COOKIE_NAME


def test_health_documentation_and_frontend(client):
    assert client.get("/api/health").json()["database"] == "ok"
    assert client.get("/openapi.json").json()["info"]["title"] == "NeonFind API"
    assert client.get("/docs").status_code == 200
    assert "NeonFind" in client.get("/").text
    assert client.get("/api/catalog/meta").json()["authentication"] == "real"


def test_signup_issues_http_only_cookie_without_exposing_password(client, register):
    user = register(client)
    assert user["demo"] is False
    assert "password" not in user
    assert client.get("/api/auth/me").json()["id"] == user["id"]
    response = client.post(
        "/api/auth/login", json={"email": "shopper@example.com", "password": "TestPass123!"}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "TestPass123!" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_duplicate_email_is_case_insensitive(client, register):
    register(client)
    assert (
        client.post(
            "/api/auth/signup",
            json={"name": "Someone", "email": "SHOPPER@example.com", "password": "OtherPass123"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "email,password", [("unknown@example.com", "WrongPass123"), ("shopper@example.com", "WrongPass123")]
)
def test_incorrect_login_is_rejected(client, register, email, password):
    register(client)
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect."


def test_logout_revokes_session_cookie_even_if_replayed(client, register):
    register(client)
    cookie = client.cookies.get(COOKIE_NAME)
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.set(COOKIE_NAME, cookie)
    assert client.get("/api/auth/me").status_code == 401


def test_user_and_session_survive_app_restart(settings, register):
    with TestClient(create_app(settings)) as first:
        user = register(first)
        cookies = dict(first.cookies)
    with TestClient(create_app(settings)) as second:
        second.cookies.update(cookies)
        assert second.get("/api/auth/me").json()["id"] == user["id"]
        assert (
            second.post(
                "/api/auth/login", json={"email": user["email"], "password": "TestPass123!"}
            ).status_code
            == 200
        )


def test_validation_does_not_echo_password_and_rejects_blank_name(client):
    response = client.post("/api/auth/signup", json={"name": "  ", "email": "invalid", "password": "S3cret"})
    assert response.status_code == 422
    assert "S3cret" not in response.text
    assert all("input" not in item for item in response.json()["detail"])


@pytest.mark.parametrize("path", ["/api/me/saved", "/api/me/searches", "/api/auth/me"])
def test_account_routes_require_login(client, path):
    assert client.get(path).status_code == 401


def test_untrusted_origin_cannot_mutate_account(client, register):
    register(client)
    assert (
        client.put("/api/me/saved/sony-xm5", headers={"Origin": "https://untrusted.example"}).status_code
        == 403
    )
    assert client.get("/api/me/saved").json()["products"] == []


def test_saved_products_and_searches_are_isolated_between_users(client, register):
    first = register(client, "first@example.com")
    first_cookie = dict(client.cookies)
    assert client.put("/api/me/saved/sony-xm5").status_code == 200
    assert client.post("/api/me/searches", json={"query": "headphones"}).status_code == 200
    client.cookies.clear()
    second = register(client, "second@example.com")
    assert first["id"] != second["id"]
    assert client.get("/api/me/saved").json()["products"] == []
    assert client.get("/api/me/searches").json()["queries"] == []
    client.delete("/api/me/saved/sony-xm5")
    client.delete("/api/me/searches")
    client.cookies.clear()
    client.cookies.update(first_cookie)
    assert client.get("/api/me/saved").json()["products"][0]["id"] == "sony-xm5"
    assert client.get("/api/me/searches").json()["queries"] == ["headphones"]


def test_save_is_idempotent_and_unknown_product_is_rejected(client, register):
    register(client)
    client.put("/api/me/saved/sony-xm5")
    client.put("/api/me/saved/sony-xm5")
    assert len(client.get("/api/me/saved").json()["products"]) == 1
    assert client.put("/api/me/saved/missing").status_code == 404
    client.delete("/api/me/saved/sony-xm5")
    assert client.get("/api/me/saved").json()["products"] == []


def test_recent_searches_are_deduplicated_and_bounded(client, register):
    register(client)
    for index in range(10):
        client.post("/api/me/searches", json={"query": f"product {index}"})
    client.post("/api/me/searches", json={"query": "  PRODUCT   9  "})
    queries = client.get("/api/me/searches").json()["queries"]
    assert len(queries) == 8 and queries[0] == "product 9"
    assert "product 0" not in queries
    client.delete("/api/me/searches")
    assert client.get("/api/me/searches").json()["queries"] == []


@pytest.mark.parametrize(
    "query,count",
    [
        ("headphones", 2),
        ("headphons", 2),
        ("airbuds", 1),
        ("smart watch", 1),
        ("laptop", 1),
        ("iPhone 19", 0),
        ("nonexistentzzzzz", 0),
    ],
)
def test_search_handles_aliases_typos_and_missing_models(client, query, count):
    response = client.get("/api/products", params={"q": query})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == count
    assert response.json()["mode"] == "demo"


def test_filters_apply_to_actual_offers_and_total_price(client):
    amazon = client.get("/api/products", params={"q": "a", "store": "amazon"}).json()["products"]
    assert len(amazon) == 4
    assert all(offer["store"] == "amazon" for product in amazon for offer in product["offers"])
    assert client.get("/api/products", params={"q": "a", "maxPrice": 0}).json()["total"] == 0
    assert (
        client.get("/api/products", params={"q": "a", "maxPrice": 2500}).json()["total"] == 0
    )  # Tote is 2499 + 150 delivery.
    assert client.get("/api/products", params={"q": "a", "maxPrice": 5000}).json()["total"] == 1
    assert client.get("/api/products", params={"q": "a", "freeShipping": True}).json()["total"] == 5
    assert client.get("/api/products", params={"q": "a", "category": "computing"}).json()["total"] == 1


def test_sorting_pagination_and_explainable_scores(client):
    data = client.get("/api/products", params={"q": "a", "sort": "price-low"}).json()
    totals = [product["offers"][0]["totalPrice"] for product in data["products"]]
    assert totals == sorted(totals)
    page = client.get("/api/products", params={"q": "a", "sort": "price-low", "offset": 2, "limit": 3}).json()
    assert page["total"] == 8 and len(page["products"]) == 3
    assert page["products"][0]["id"] == data["products"][2]["id"]
    for product in data["products"]:
        assert 0 <= product["score"] <= 100
        assert product["recommendation"]["method"] == "weighted-rules-v2"
        assert len(product["recommendation"]["reasons"]) >= 3
        assert abs(sum(product["recommendation"]["weights"].values()) - 1) < 0.0001


def test_cached_catalog_is_not_mutated_by_filters(client):
    first = client.get("/api/products", params={"q": "headphones"}).json()
    assert first["cache"]["hit"] is False
    filtered = client.get("/api/products", params={"q": "headphones", "store": "amazon"}).json()
    assert filtered["cache"]["hit"] is True and filtered["total"] == 1
    original = client.get("/api/products", params={"q": "headphones"}).json()
    assert original["total"] == 2
    assert max(len(product["offers"]) for product in original["products"]) == 4


def test_offers_include_delivery_and_comparison_limits(client):
    offers = client.get("/api/products/sony-xm5/offers").json()["offers"]
    assert all(offer["totalPrice"] == offer["price"] + offer["shippingFee"] for offer in offers)
    assert [offer["totalPrice"] for offer in offers] == sorted(offer["totalPrice"] for offer in offers)
    comparison = client.post("/api/products/compare", json={"ids": ["sony-xm5", "airpods-pro"]}).json()
    assert [product["id"] for product in comparison["products"]] == ["sony-xm5", "airpods-pro"]
    assert comparison["lowestTotalPrice"] == 59999
    assert client.post("/api/products/compare", json={"ids": ["sony-xm5"] * 5}).status_code == 422
    assert client.post("/api/products/compare", json={"ids": ["sony-xm5"] * 2}).status_code == 422
    assert client.get("/api/products/missing/offers").status_code == 404


def test_auth_rate_limit_has_a_working_second_request(settings):
    limited = settings.model_copy(update={"auth_limit_per_minute": 2})
    with TestClient(create_app(limited)) as client:
        body = {"email": "missing@example.com", "password": "WrongPass"}
        assert client.post("/api/auth/login", json=body).status_code == 401
        assert client.post("/api/auth/login", json=body).status_code == 401
        response = client.post("/api/auth/login", json=body)
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_production_requires_a_signing_secret():
    with pytest.raises(ValidationError):
        Settings(environment="production", jwt_secret=None)
