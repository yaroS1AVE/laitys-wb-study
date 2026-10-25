"""Real PostgreSQL migration, consistency, and recovery checks."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from decimal import Decimal
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app_final import create_app


def legacy_expense(identifier="legacy-only", amount="123.45"):
    return {"id": identifier, "date": "2026-10-05", "category": "Прочее",
            "amount": amount, "comment": "Сохранённый учебный расход"}


def write_legacy(directory, records):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "expenses.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return path


def test_first_start_materializes_demo_in_real_sql_tables(app_factory, db_connection):
    with TestClient(app_factory()) as client:
        assert client.get("/health").json()["database"] == "postgresql"
        assert db_connection.execute("SELECT count(*) FROM products").fetchone()[0] == 6
        assert db_connection.execute("SELECT count(*) FROM sales").fetchone()[0] == 28
        assert db_connection.execute("SELECT count(*) FROM expenses").fetchone()[0] == 2
        stored_money = db_connection.execute("SELECT price_cents, cost_cents FROM products WHERE sku = %s",
                                             ("WB-DEMO-001",)).fetchone()
        assert stored_money == (219000, 102000)
        assert all(type(value) is int for value in stored_money)
        assert type(db_connection.execute("SELECT amount_cents FROM expenses LIMIT 1").fetchone()[0]) is int
        assert db_connection.execute("SELECT sum(amount_cents) FROM expenses").fetchone()[0] == 255000


def test_legacy_file_is_imported_as_complete_snapshot_once(app_factory, tmp_path, db_connection):
    records = [legacy_expense("demo-advertising-001", "1.23"), legacy_expense("custom", "0.01")]
    path = write_legacy(tmp_path, records)
    original = path.read_bytes()
    with TestClient(app_factory()) as client:
        items = client.get("/api/expenses").json()["items"]
        assert {item["id"] for item in items} == {"demo-advertising-001", "custom"}
        assert db_connection.execute("SELECT id, amount_cents FROM expenses ORDER BY id").fetchall() == [
            ("custom", 1), ("demo-advertising-001", 123)]
    assert path.read_bytes() == original
    # A legacy backup is no longer the source of truth after a successful import.
    path.write_text("[]", encoding="utf-8")
    with TestClient(app_factory()) as restarted:
        assert restarted.get("/api/expenses").json()["items"] == items


def test_empty_legacy_snapshot_stays_empty_after_restart(app_factory, tmp_path):
    path = write_legacy(tmp_path, [])
    for _ in range(2):
        with TestClient(app_factory()) as client:
            assert client.get("/api/expenses").json() == {"items": [], "total": 0}
            assert client.get("/api/report").json()["totals"]["expenses"] == 0
    assert path.read_text(encoding="utf-8") == "[]"


def test_deleting_all_expenses_does_not_restore_seed_on_restart(app_factory):
    with TestClient(app_factory()) as client:
        for expense in client.get("/api/expenses").json()["items"]:
            assert client.delete(f"/api/expenses/{expense['id']}").status_code == 204
    with TestClient(app_factory()) as restarted:
        assert restarted.get("/api/expenses").json() == {"items": [], "total": 0}
        assert restarted.get("/api/report").json()["totals"]["profit"] == 77572


@pytest.mark.parametrize("invalid", [
    "broken JSON", {"expenses": []},
    [legacy_expense(), legacy_expense()],
    [legacy_expense(), {**legacy_expense("invalid"), "amount": "-1"}],
])
def test_failed_legacy_import_rolls_back_all_tables_and_can_be_retried(
        app_factory, tmp_path, postgres_url, database_schema, invalid):
    if invalid == "broken JSON":
        path = tmp_path / "expenses.json"
        path.write_text("{invalid", encoding="utf-8")
    else:
        path = write_legacy(tmp_path, invalid)
    original = path.read_bytes()
    with pytest.raises((RuntimeError, ValueError, psycopg.Error)):
        with TestClient(app_factory()):
            pass
    assert path.read_bytes() == original
    with psycopg.connect(postgres_url) as connection:
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('products', 'sales', 'expenses')",
            (database_schema,)).fetchall()
        for (table,) in tables:
            count = connection.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(database_schema), sql.Identifier(table))).fetchone()[0]
            assert count == 0
    write_legacy(tmp_path, [legacy_expense("retry", "0.01")])
    with TestClient(app_factory()) as client:
        assert [row["id"] for row in client.get("/api/expenses").json()["items"]] == ["retry"]
        assert client.get("/api/products").json()["total"] == 6


def test_committed_external_sql_changes_are_visible_in_all_api_reads(app_factory, db_connection):
    with TestClient(app_factory()) as client:
        sku = "WB-DEMO-001"
        db_connection.execute(
            "UPDATE products SET name = %s, price_cents = %s, cost_cents = %s, stock = %s WHERE sku = %s",
            ("Товар из PostgreSQL", 219001, 102001, 9, sku))
        db_connection.commit()
        product = client.get(f"/api/products/{sku}").json()
        assert (product["name"], product["price"], product["cost"], product["stock"]) == (
            "Товар из PostgreSQL", 2190.01, 1020.01, 9)
        assert client.get("/api/products", params={"q": "PostgreSQL"}).json()["items"] == [product]
        before = client.get("/api/report").json()["totals"]
        db_connection.execute("UPDATE sales SET quantity = quantity + 1 WHERE id = "
                              "(SELECT min(id) FROM sales WHERE sku = %s)", (sku,))
        db_connection.commit()
        assert client.get(f"/api/products/{sku}").json()["units_sold"] == product["units_sold"] + 1
        after_sale = client.get("/api/report").json()["totals"]
        assert after_sale["units"] == before["units"] + 1
        assert Decimal(str(after_sale["revenue"])) - Decimal(str(before["revenue"])) == Decimal("2190.01")
        expense = client.get("/api/expenses").json()["items"][0]
        db_connection.execute("UPDATE expenses SET amount_cents = amount_cents + 1 WHERE id = %s",
                              (expense["id"],))
        db_connection.commit()
        after_expense = client.get("/api/report").json()["totals"]
        assert Decimal(str(after_sale["profit"])) - Decimal(str(after_expense["profit"])) == Decimal("0.01")
        changed = next(item for item in client.get("/api/expenses").json()["items"] if item["id"] == expense["id"])
        assert Decimal(str(changed["amount"])) - Decimal(str(expense["amount"])) == Decimal("0.01")
        assert client.get("/api/export").status_code == 200


@pytest.mark.parametrize("statement, values, error_type", [
    ("UPDATE products SET price_cents = -1 WHERE sku = %s", ("WB-DEMO-001",), psycopg.errors.CheckViolation),
    ("UPDATE products SET cost_cents = -1 WHERE sku = %s", ("WB-DEMO-001",), psycopg.errors.CheckViolation),
    ("UPDATE products SET stock = -1 WHERE sku = %s", ("WB-DEMO-001",), psycopg.errors.CheckViolation),
    ("UPDATE sales SET quantity = 0", (), psycopg.errors.CheckViolation),
    ("UPDATE sales SET sku = %s", ("not-a-product",), psycopg.errors.ForeignKeyViolation),
    ("UPDATE expenses SET amount_cents = 0", (), psycopg.errors.CheckViolation),
    ("UPDATE expenses SET category = %s", ("invalid-category",), psycopg.errors.CheckViolation),
])
def test_database_constraints_reject_invalid_data_without_changing_report(
        app_factory, db_connection, statement, values, error_type):
    with TestClient(app_factory()) as client:
        before = client.get("/api/report").json()
        with pytest.raises(error_type):
            with db_connection.transaction():
                db_connection.execute(statement, values)
        assert client.get("/api/report").json() == before


def test_two_applications_can_initialize_same_schema_without_duplicate_seed(app_factory, db_connection):
    ready = Barrier(2)

    def start_and_read(_):
        application = app_factory()
        ready.wait(timeout=10)
        with TestClient(application) as client:
            response = client.get("/api/report")
            assert response.status_code == 200
            return response.json()["totals"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(start_and_read, range(2)))
    assert first == second
    assert db_connection.execute("SELECT count(*) FROM products").fetchone()[0] == 6
    assert db_connection.execute("SELECT count(*) FROM sales").fetchone()[0] == 28
    assert db_connection.execute("SELECT count(*) FROM expenses").fetchone()[0] == 2
    assert db_connection.execute("SELECT schema_version, initialized FROM app_meta WHERE id = 1").fetchone() == (1, True)


@pytest.mark.parametrize("marker, value", [("schema_version", 2), ("initialized", False)])
def test_incompatible_or_reset_initialization_marker_fails_without_overwriting_data(
        app_factory, db_connection, marker, value):
    with TestClient(app_factory()) as client:
        assert client.get("/health").status_code == 200
    before = {table: db_connection.execute(sql.SQL("SELECT * FROM {} ORDER BY 1").format(
        sql.Identifier(table))).fetchall() for table in ("products", "sales", "expenses")}
    db_connection.execute(sql.SQL("UPDATE app_meta SET {} = %s WHERE id = 1").format(
        sql.Identifier(marker)), (value,))
    db_connection.commit()
    with pytest.raises(RuntimeError):
        with TestClient(app_factory()):
            pass
    after = {table: db_connection.execute(sql.SQL("SELECT * FROM {} ORDER BY 1").format(
        sql.Identifier(table))).fetchall() for table in before}
    assert after == before
    assert db_connection.execute(sql.SQL("SELECT {} FROM app_meta WHERE id = 1").format(
        sql.Identifier(marker))).fetchone()[0] == value


def test_concurrent_writes_from_two_applications_are_not_lost(app_factory, db_connection):
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app_factory())) for _ in range(2)]
        before = clients[0].get("/api/report").json()["totals"]

        def add(index):
            response = clients[index % 2].post("/api/expenses", json={
                "date": "2026-10-05", "category": "Прочее", "amount": "0.01",
                "comment": f"Concurrent expense {index}",
            })
            assert response.status_code == 201
            return response.json()["id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            identifiers = list(pool.map(add, range(24)))
        assert len(set(identifiers)) == 24
        assert db_connection.execute("SELECT count(*) FROM expenses").fetchone()[0] == 26
        after = clients[1].get("/api/report").json()["totals"]
        assert Decimal(str(before["profit"])) - Decimal(str(after["profit"])) == Decimal("0.24")
        assert Decimal(str(after["expenses"])) - Decimal(str(before["expenses"])) == Decimal("0.24")


def test_missing_table_returns_503_and_restart_does_not_silently_restore_it(app_factory, db_connection):
    with TestClient(app_factory()) as client:
        assert client.get("/health").status_code == 200
        db_connection.execute("DROP TABLE expenses")
        db_connection.commit()
        for route in ("/health", "/api/expenses", "/api/report", "/api/export"):
            response = client.get(route)
            assert response.status_code == 503
            assert response.json()["detail"]
    with pytest.raises(RuntimeError):
        with TestClient(app_factory()):
            pass
    assert db_connection.execute("SELECT to_regclass('expenses')").fetchone()[0] is None
    assert db_connection.execute("SELECT count(*) FROM products").fetchone()[0] == 6
    assert db_connection.execute("SELECT count(*) FROM sales").fetchone()[0] == 28
    assert db_connection.execute("SELECT schema_version, initialized FROM app_meta WHERE id = 1").fetchone() == (1, True)


def test_explicit_database_url_takes_priority_over_environment(monkeypatch, app_factory):
    monkeypatch.setenv("LAITYS_DATABASE_URL", "postgresql://unavailable@127.0.0.1:1/unavailable?connect_timeout=1")
    with TestClient(app_factory()) as client:
        assert client.get("/health").status_code == 200


def test_missing_database_url_fails_startup_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("LAITYS_DATABASE_URL", "")
    application = create_app(data_dir=tmp_path)
    with pytest.raises(RuntimeError) as error:
        with TestClient(application):
            pass
    assert "LAITYS_DATABASE_URL" in str(error.value)


def test_unreachable_database_fails_startup_without_disclosing_password(tmp_path, caplog):
    secret = "password-that-must-not-be-logged"
    application = create_app(data_dir=tmp_path, database_url=(
        f"postgresql://test:{secret}@127.0.0.1:1/unavailable?connect_timeout=1"))
    with pytest.raises(RuntimeError) as error:
        with TestClient(application):
            pass
    assert "postgres" in str(error.value).lower()
    assert secret not in str(error.value)
    assert secret not in caplog.text


@pytest.mark.parametrize("name", ["public; DROP SCHEMA public CASCADE; --", "", "test-name", "x" * 64])
def test_unsafe_or_invalid_schema_names_are_rejected_before_sql(tmp_path, name):
    with pytest.raises(ValueError):
        create_app(data_dir=tmp_path, database_url="postgresql://unused@127.0.0.1:1/unused",
                   database_schema=name)
