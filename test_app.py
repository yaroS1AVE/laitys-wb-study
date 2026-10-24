"""HTTP checks for the standalone, synthetic Wildberries study project.

Every application uses its own temporary directory. These tests never request
marketplace APIs or use the working LAITYS service and its private database.
"""

import csv
import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app_final import create_app


def money(value):
    """Compare rubles as decimals, without introducing float arithmetic."""
    return Decimal(str(value))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as test_client:
        yield test_client


def test_health_reports_demo_mode_and_shop(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["marketplace"] == "wildberries"
    assert body["mode"] == "demo"
    assert isinstance(body["shop_name"], str) and body["shop_name"].strip()


def test_version_identifies_application(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["version"] == "1.0.0"
    assert isinstance(response.json()["name"], str)
    assert response.json()["name"].strip()


def test_products_have_complete_financial_fields(client):
    response = client.get("/api/products")
    assert response.status_code == 200
    body = response.json()
    assert body["marketplace"] == "wildberries"
    assert body["total"] == len(body["items"])
    assert body["total"] >= 3
    required = {"sku", "name", "price", "cost", "stock", "units_sold",
                "revenue", "profit", "margin"}
    for product in body["items"]:
        assert required <= product.keys()
        assert product["sku"] and product["name"]
        assert product["stock"] >= 0
        assert product["units_sold"] >= 0


def test_products_can_be_filtered_by_sku_and_name(client):
    first = client.get("/api/products").json()["items"][0]
    for query in (first["sku"].lower(), first["name"].swapcase()):
        response = client.get("/api/products", params={"q": query})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == len(body["items"])
        assert first["sku"] in {item["sku"] for item in body["items"]}
        assert all(query.casefold() in (item["sku"] + " " + item["name"]).casefold()
                   for item in body["items"])


def test_unmatched_product_filter_returns_empty_collection(client):
    response = client.get("/api/products", params={"q": "nonexistent-sku-xyz123"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_product_by_sku_matches_catalog(client):
    product = client.get("/api/products").json()["items"][0]
    response = client.get(f"/api/products/{product['sku']}")
    assert response.status_code == 200
    assert response.json() == product


def test_unknown_product_returns_not_found(client):
    response = client.get("/api/products/nonexistent-sku-xyz123")
    assert response.status_code == 404
    assert response.json()["detail"]


def test_report_totals_reconcile_with_daily_values_and_costs(client):
    response = client.get("/api/report")
    assert response.status_code == 200
    body = response.json()
    assert body["marketplace"] == "wildberries"
    assert body["period"] == {"from": "2026-10-01", "to": "2026-10-14"}
    totals = body["totals"]
    assert totals["units"] > 0
    assert money(totals["revenue"]) > 0
    assert money(totals["profit"]) == (
        money(totals["revenue"]) - money(totals["commission"])
        - money(totals["logistics"]) - money(totals["cost"])
        - money(totals["expenses"])
    )
    assert sum((money(day["revenue"]) for day in body["daily"]), Decimal(0)) == money(totals["revenue"])
    assert sum((money(day["profit"]) for day in body["daily"]), Decimal(0)) == money(totals["profit"])
    expected_margin = float(money(totals["profit"]) / money(totals["revenue"]) * 100)
    assert totals["margin"] == pytest.approx(expected_margin, abs=0.01)


def test_custom_report_period_is_applied(client):
    response = client.get("/api/report", params={"date_from": "2026-10-03", "date_to": "2026-10-07"})
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == {"from": "2026-10-03", "to": "2026-10-07"}
    assert all("2026-10-03" <= day["date"] <= "2026-10-07" for day in body["daily"])


def test_reversed_report_dates_are_rejected(client):
    response = client.get("/api/report", params={"date_from": "2026-10-14", "date_to": "2026-10-01"})
    assert response.status_code == 400
    assert response.json()["detail"]


@pytest.mark.parametrize("invalid_date", ["not-a-date", "2026-02-30"])
def test_invalid_report_date_is_rejected(client, invalid_date):
    response = client.get("/api/report", params={"date_from": invalid_date})
    assert response.status_code == 422


def test_period_without_data_has_zero_totals(client):
    response = client.get("/api/report", params={"date_from": "2025-01-01", "date_to": "2025-01-02"})
    assert response.status_code == 200
    body = response.json()
    assert body["daily"] == []
    assert all(money(value) == 0 for value in body["totals"].values())


def test_expense_changes_profit_and_persists_after_restart(tmp_path):
    payload = {"date": "2026-10-05", "category": "Прочее", "amount": "123.45",
               "comment": "Учебный расход для проверки сохранения"}
    with TestClient(create_app(data_dir=tmp_path)) as first_client:
        before = first_client.get("/api/report").json()["totals"]
        response = first_client.post("/api/expenses", json=payload)
        assert response.status_code == 201
        expense = response.json()
        assert expense["id"]
        assert money(expense["amount"]) == Decimal("123.45")
        assert expense["comment"] == payload["comment"]
        after = first_client.get("/api/report").json()["totals"]
        assert money(after["expenses"]) - money(before["expenses"]) == Decimal("123.45")
        assert money(before["profit"]) - money(after["profit"]) == Decimal("123.45")

    with TestClient(create_app(data_dir=tmp_path)) as second_client:
        saved = second_client.get("/api/expenses").json()
        assert saved["total"] == len(saved["items"])
        assert any(item["id"] == expense["id"] for item in saved["items"])
        assert second_client.get("/api/report").json()["totals"] == after
        assert second_client.delete(f"/api/expenses/{expense['id']}").status_code == 204
        assert second_client.get("/api/report").json()["totals"] == before

    with TestClient(create_app(data_dir=tmp_path)) as third_client:
        assert all(item["id"] != expense["id"] for item in third_client.get("/api/expenses").json()["items"])
        assert third_client.delete(f"/api/expenses/{expense['id']}").status_code == 404


@pytest.mark.parametrize("amount", [0, -1, "1000000.01", "1.234"])
def test_invalid_expense_amount_does_not_change_expenses(client, amount):
    before = client.get("/api/expenses").json()
    response = client.post("/api/expenses", json={
        "date": "2026-10-05", "category": "Прочее", "amount": amount,
        "comment": "Недопустимая сумма",
    })
    assert response.status_code == 422
    assert client.get("/api/expenses").json() == before


@pytest.mark.parametrize("change", [
    {"date": "2026-02-30"}, {"category": "Несуществующая категория"}, {"comment": "x" * 201},
])
def test_invalid_expense_fields_are_rejected(client, change):
    payload = {"date": "2026-10-05", "category": "Реклама", "amount": "100.00", "comment": ""}
    payload.update(change)
    assert client.post("/api/expenses", json=payload).status_code == 422


def test_expense_outside_report_period_does_not_change_profit(client):
    before = client.get("/api/report").json()["totals"]
    response = client.post("/api/expenses", json={
        "date": "2026-11-01", "category": "Упаковка", "amount": "75.00", "comment": "Другой период",
    })
    assert response.status_code == 201
    assert client.get("/api/report").json()["totals"] == before


def test_shop_name_can_be_configured_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LAITYS_SHOP_NAME", "Учебный магазин WB")
    with TestClient(create_app(data_dir=tmp_path)) as configured_client:
        assert configured_client.get("/health").json()["shop_name"] == "Учебный магазин WB"


def test_environment_data_directory_is_used_and_explicit_path_takes_priority(monkeypatch, tmp_path):
    environment_dir = tmp_path / "configured-by-env"
    explicit_dir = tmp_path / "configured-by-argument"
    monkeypatch.setenv("LAITYS_DATA_DIR", str(environment_dir))
    payload = {"date": "2026-10-05", "category": "Доставка", "amount": "25.00", "comment": "ENV isolation"}
    with TestClient(create_app()) as environment_client:
        response = environment_client.post("/api/expenses", json=payload)
        assert response.status_code == 201
        identifier = response.json()["id"]
    with TestClient(create_app()) as reloaded_client:
        assert any(item["id"] == identifier for item in reloaded_client.get("/api/expenses").json()["items"])
    with TestClient(create_app(data_dir=explicit_dir)) as explicit_client:
        assert not any(item["id"] == identifier for item in explicit_client.get("/api/expenses").json()["items"])
    assert environment_dir.is_dir()
    assert explicit_dir.is_dir()


def test_export_is_csv_with_utf8_bom(client):
    response = client.get("/api/export")
    assert response.status_code == 200
    assert "csv" in response.headers["content-type"]
    assert response.content.startswith(b"\xef\xbb\xbf")
    decoded = response.content.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(decoded, delimiters=",;")
    rows = list(csv.reader(io.StringIO(decoded), dialect))
    assert len(rows) >= 2
    assert len(rows[0]) >= 2
    assert all(len(row) == len(rows[0]) for row in rows)


def test_homepage_and_api_documentation_are_available(client):
    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "text/html" in homepage.headers["content-type"]
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert {"/health", "/version", "/api/products", "/api/report", "/api/expenses"} <= schema.json()["paths"].keys()
