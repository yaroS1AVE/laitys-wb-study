"""PostgreSQL-хранилище учебного магазина; JSON нужен только для первого запуска."""

import hashlib
import json
import re
from datetime import date as Date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
PROJECT = Path(__file__).resolve().parent


def cents(value: int | float | str | Decimal) -> int:
    """Внутри расчётов деньги хранятся в целых копейках."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rubles(value: int) -> float:
    return float(Decimal(value) / 100)


class ExpenseInput(BaseModel):
    date: Date
    category: Literal["Реклама", "Упаковка", "Доставка", "Прочее"]
    amount: Decimal = Field(gt=0, le=Decimal("1000000"), decimal_places=2)
    comment: str = Field(default="", max_length=200)


class DatabaseInitializationError(RuntimeError):
    """Понятная ошибка запуска без паролей и других частей строки подключения."""


class PostgresStore:
    def __init__(self, directory: Path, database_url: str | None, schema: str = "public"):
        if not isinstance(schema, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", schema):
            raise ValueError("Имя схемы: до 63 ASCII-букв, цифр и подчёркиваний; первая — буква или подчёркивание")
        self.directory = Path(directory)
        self.database_url = database_url
        self.schema = schema
        self.legacy_expenses_path = self.directory / "expenses.json"
        self.seed_path = PROJECT / "data" / "demo.json"
        self.initialized = False

    def _table(self, name: str):
        return sql.Identifier(self.schema, name)

    def _connect(self):
        if not self.database_url or not self.database_url.strip():
            raise DatabaseInitializationError("Укажите LAITYS_DATABASE_URL для подключения к PostgreSQL")
        return psycopg.connect(self.database_url, connect_timeout=5, row_factory=dict_row)

    def _ensure_initialized(self) -> None:
        if not self.initialized:
            raise RuntimeError("Хранилище ещё не инициализировано")

    def initialize(self) -> None:
        """DDL, импорт и маркер фиксируются одной транзакцией и только один раз."""
        try:
            with self._connect() as connection:
                # Стабильный ключ работает и между разными процессами приложения.
                lock_key = int.from_bytes(hashlib.sha256(("laitys:" + self.schema).encode()).digest()[:8], "big", signed=True)
                connection.execute("SET LOCAL lock_timeout = '10s'")
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
                connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
                self._create_metadata_table(connection)
                connection.execute(sql.SQL("INSERT INTO {} (id, schema_version, initialized) VALUES (1, %s, false) ON CONFLICT (id) DO NOTHING")
                                   .format(self._table("app_meta")), (SCHEMA_VERSION,))
                marker = connection.execute(sql.SQL("SELECT schema_version, initialized FROM {} WHERE id = 1")
                                            .format(self._table("app_meta"))).fetchone()
                if marker["schema_version"] != SCHEMA_VERSION:
                    raise DatabaseInitializationError("Неподдерживаемая версия схемы PostgreSQL")
                if marker["initialized"]:
                    # Потерянные таблицы существующей базы нельзя молча заменить пустыми.
                    self._check_tables(connection)
                else:
                    self._create_tables(connection)
                    for table in ("products", "sales", "expenses"):
                        exists = connection.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {}) AS present")
                                                    .format(self._table(table))).fetchone()["present"]
                        if exists:
                            raise DatabaseInitializationError("В неинициализированной схеме уже есть данные; используйте отдельную схему учебного проекта")
                    self._bootstrap(connection)
                    connection.execute(sql.SQL("UPDATE {} SET initialized = true WHERE id = 1")
                                       .format(self._table("app_meta")))
            # Локальная готовность меняется только после успешного COMMIT.
            self.initialized = True
        except DatabaseInitializationError:
            raise
        except psycopg.Error:
            raise DatabaseInitializationError("Не удалось инициализировать PostgreSQL; проверьте LAITYS_DATABASE_URL, доступность сервера и права на схему") from None
        except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
            raise DatabaseInitializationError("Не удалось прочитать демоданные или перенести expenses.json; исходный JSON сохранён") from None

    def _create_tables(self, connection) -> None:
        connection.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {} (
            sku TEXT PRIMARY KEY CHECK (length(sku) > 0),
            name TEXT NOT NULL CHECK (length(name) > 0),
            price_cents BIGINT NOT NULL CHECK (price_cents >= 0),
            cost_cents BIGINT NOT NULL CHECK (cost_cents >= 0),
            stock INTEGER NOT NULL CHECK (stock >= 0)
        )""").format(self._table("products")))
        connection.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {} (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            date DATE NOT NULL,
            sku TEXT NOT NULL REFERENCES {} (sku),
            quantity INTEGER NOT NULL CHECK (quantity > 0)
        )""").format(self._table("sales"), self._table("products")))
        connection.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {} (
            id TEXT PRIMARY KEY CHECK (length(id) > 0),
            date DATE NOT NULL,
            category TEXT NOT NULL CHECK (category IN ('Реклама', 'Упаковка', 'Доставка', 'Прочее')),
            amount_cents BIGINT NOT NULL CHECK (amount_cents BETWEEN 1 AND 100000000),
            comment VARCHAR(200) NOT NULL DEFAULT ''
        )""").format(self._table("expenses")))

    def _create_metadata_table(self, connection) -> None:
        connection.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {} (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            schema_version INTEGER NOT NULL CHECK (schema_version > 0),
            initialized BOOLEAN NOT NULL
        )""").format(self._table("app_meta")))

    def _check_tables(self, connection) -> None:
        columns = {
            "products": ("sku", "name", "price_cents", "cost_cents", "stock"),
            "sales": ("id", "date", "sku", "quantity"),
            "expenses": ("id", "date", "category", "amount_cents", "comment"),
        }
        for table, fields in columns.items():
            connection.execute(sql.SQL("SELECT {} FROM {} LIMIT 0").format(
                sql.SQL(", ").join(sql.Identifier(field) for field in fields), self._table(table)))

    def _bootstrap(self, connection) -> None:
        demo = json.loads(self.seed_path.read_text(encoding="utf-8"))
        # Существующий файл — полный снимок, даже если все расходы уже удалены.
        raw_expenses = (json.loads(self.legacy_expenses_path.read_text(encoding="utf-8"))
                        if self.legacy_expenses_path.exists() else demo["expenses"])
        if not isinstance(raw_expenses, list):
            raise ValueError("Expected expense list")
        expenses, seen = [], set()
        for row in raw_expenses:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
                raise ValueError("Invalid or duplicate expense identifier")
            item = ExpenseInput(**row)
            seen.add(row["id"])
            expenses.append((row["id"], item.date, item.category, cents(item.amount), item.comment))
        with connection.cursor() as cursor:
            cursor.executemany(sql.SQL("INSERT INTO {} (sku, name, price_cents, cost_cents, stock) VALUES (%s, %s, %s, %s, %s)")
                               .format(self._table("products")),
                               [(row["sku"], row["name"], cents(row["price"]), cents(row["cost"]), row["stock"])
                                for row in demo["products"]])
            cursor.executemany(sql.SQL("INSERT INTO {} (date, sku, quantity) VALUES (%s, %s, %s)")
                               .format(self._table("sales")),
                               [(Date.fromisoformat(row["date"]), row["sku"], row["quantity"]) for row in demo["sales"]])
            cursor.executemany(sql.SQL("INSERT INTO {} (id, date, category, amount_cents, comment) VALUES (%s, %s, %s, %s, %s)")
                               .format(self._table("expenses")), expenses)

    @staticmethod
    def _expense(row: dict) -> dict:
        return {"id": row["id"], "date": row["date"].isoformat(), "category": row["category"],
                "amount": rubles(row["amount_cents"]), "comment": row["comment"]}

    def _catalog(self, connection) -> dict:
        products = connection.execute(sql.SQL("SELECT sku, name, price_cents, cost_cents, stock FROM {} ORDER BY sku")
                                      .format(self._table("products"))).fetchall()
        sales = connection.execute(sql.SQL("SELECT id, date, sku, quantity FROM {} ORDER BY date, id")
                                   .format(self._table("sales"))).fetchall()
        return {"products": products, "sales": [{**row, "date": row["date"].isoformat()} for row in sales]}

    def _expenses(self, connection) -> list[dict]:
        rows = connection.execute(sql.SQL("SELECT id, date, category, amount_cents, comment FROM {} ORDER BY date DESC, id DESC")
                                  .format(self._table("expenses"))).fetchall()
        return [self._expense(row) for row in rows]

    def read_catalog(self) -> dict:
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return self._catalog(connection)

    def snapshot(self) -> dict:
        """Единый SQL-снимок: конкурентная запись не разделит итоги одного отчёта."""
        self._ensure_initialized()
        with self._connect() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return {**self._catalog(connection), "expenses": self._expenses(connection)}

    def read_expenses(self) -> list[dict]:
        self._ensure_initialized()
        with self._connect() as connection:
            return self._expenses(connection)

    def add_expense(self, expense: ExpenseInput) -> dict:
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(sql.SQL("INSERT INTO {} (id, date, category, amount_cents, comment) VALUES (%s, %s, %s, %s, %s) RETURNING id, date, category, amount_cents, comment")
                                     .format(self._table("expenses")),
                                     (uuid4().hex, expense.date, expense.category, cents(expense.amount), expense.comment)).fetchone()
            return self._expense(row)

    def delete_expense(self, expense_id: str) -> bool:
        self._ensure_initialized()
        with self._connect() as connection:
            cursor = connection.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(self._table("expenses")), (expense_id,))
            return cursor.rowcount > 0

    def check_health(self) -> None:
        self._ensure_initialized()
        with self._connect() as connection:
            self._check_tables(connection)
            marker = connection.execute(sql.SQL("SELECT schema_version, initialized FROM {} WHERE id = 1")
                                        .format(self._table("app_meta"))).fetchone()
            if not marker or not marker["initialized"] or marker["schema_version"] != SCHEMA_VERSION:
                raise RuntimeError("Схема PostgreSQL не инициализирована")
