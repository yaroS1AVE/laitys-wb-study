"""Учебный LAITYS: аналитика Wildberries на вымышленных данных."""

import csv
import io
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date as Date
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT = Path(__file__).resolve().parent
VERSION = "1.0.0"
PERIOD_FROM, PERIOD_TO = Date(2026, 10, 1), Date(2026, 10, 14)
logger = logging.getLogger("laitys")


def cents(value: int | float | str | Decimal) -> int:
    """Внутри расчётов деньги хранятся в целых копейках."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rubles(value: int) -> float:
    return float(Decimal(value) / 100)


def margin(profit: int, revenue: int) -> float:
    return float((Decimal(profit) * 100 / revenue).quantize(Decimal("0.01"))) if revenue else 0.0


class ExpenseInput(BaseModel):
    date: Date
    category: Literal["Реклама", "Упаковка", "Доставка", "Прочее"]
    amount: Decimal = Field(gt=0, le=Decimal("1000000"), decimal_places=2)
    comment: str = Field(default="", max_length=200)


def expense_record(expense: ExpenseInput, expense_id: str) -> dict:
    return {"id": expense_id, "date": expense.date.isoformat(),
            "category": expense.category, "amount": float(expense.amount),
            "comment": expense.comment}


class DemoStore:
    """Неизменяемая демобаза и отдельный файл редактируемых расходов."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.expenses_path = directory / "expenses.json"
        self.lock = threading.RLock()
        self.seed: dict | None = None

    def initialize(self) -> None:
        with self.lock:
            self.seed = json.loads((PROJECT / "data" / "demo.json").read_text(encoding="utf-8"))
            self.directory.mkdir(parents=True, exist_ok=True)
            if not self.expenses_path.exists():
                self._write(self.seed["expenses"])
            self.read_expenses()  # Не запускать приложение с повреждённым хранилищем.

    def read_expenses(self) -> list[dict]:
        with self.lock:
            if self.seed is None:
                raise RuntimeError("Storage is not initialized")
            records = json.loads(self.expenses_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Expected expense list")
            result = []
            for row in records:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    raise ValueError("Invalid expense record")
                result.append(expense_record(ExpenseInput(**row), row["id"]))
            return sorted(result, key=lambda row: (row["date"], row["id"]), reverse=True)

    def _write(self, records: list[dict]) -> None:
        temporary = self.directory / f".expenses-{uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(records, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.expenses_path)
        finally:
            temporary.unlink(missing_ok=True)

    def add_expense(self, expense: ExpenseInput) -> dict:
        with self.lock:
            records = self.read_expenses()
            item = expense_record(expense, uuid4().hex)
            self._write([*records, item])
            return item

    def delete_expense(self, expense_id: str) -> bool:
        with self.lock:
            records = self.read_expenses()
            remaining = [item for item in records if item["id"] != expense_id]
            if len(records) == len(remaining):
                return False
            self._write(remaining)
            return True


def create_app(data_dir: Path | None = None) -> FastAPI:
    """Фабрика приложения; отдельный каталог позволяет изолировать тесты."""
    load_dotenv(PROJECT / ".env")
    shop_name = os.getenv("LAITYS_SHOP_NAME", "LAITYS · учебный магазин")
    configured_dir = Path(data_dir) if data_dir is not None else Path(os.getenv("LAITYS_DATA_DIR", ".runtime"))
    runtime_dir = configured_dir if configured_dir.is_absolute() else PROJECT / configured_dir
    level = os.getenv("LAITYS_LOG_LEVEL", "INFO").upper()
    log_level = level if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.setLevel(log_level)
    store = DemoStore(runtime_dir)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        logger.info("startup version=%s marketplace=wildberries mode=demo", VERSION)
        try:
            yield
        finally:
            logger.info("shutdown version=%s", VERSION)

    application = FastAPI(title="LAITYS Wildberries · учебная версия", version=VERSION, lifespan=lifespan)
    application.state.data_dir = runtime_dir
    application.state.shop_name = shop_name
    application.state.storage = store
    application.mount("/static", StaticFiles(directory=PROJECT / "static", check_dir=False), name="static")

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Переполнение числа даёт inf; даже ответ об ошибке должен быть корректным JSON.
        errors = jsonable_encoder(exc.errors(), custom_encoder={
            float: lambda value: value if isfinite(value) else str(value),
        })
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.middleware("http")
    async def request_log(request: Request, call_next):
        started, status = perf_counter(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            logger.info("request method=%s path=%s status=%s duration_ms=%.2f",
                        request.method, request.url.path, status, (perf_counter() - started) * 1000)

    def storage_call(operation, *args):
        try:
            return operation(*args)
        except (OSError, ValueError, TypeError, RuntimeError):
            logger.error("storage result=unavailable")
            raise HTTPException(status_code=503, detail="Хранилище временно недоступно") from None

    def seed() -> dict:
        if store.seed is None:
            raise HTTPException(status_code=503, detail="Приложение ещё не запущено")
        return store.seed

    def sale_amounts(sale: dict, products: dict) -> dict:
        product, units = products[sale["sku"]], sale["quantity"]
        revenue = cents(product["price"]) * units
        commission = int((Decimal(revenue) * Decimal("0.20")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        logistics, cost = 3500 * units, cents(product["cost"]) * units
        return {"units": units, "revenue": revenue, "commission": commission,
                "logistics": logistics, "cost": cost,
                "profit": revenue - commission - logistics - cost}

    def product_items() -> list[dict]:
        demo = seed()
        products = {product["sku"]: product for product in demo["products"]}
        totals = {sku: {"units": 0, "revenue": 0, "profit": 0} for sku in products}
        for sale in demo["sales"]:
            amounts = sale_amounts(sale, products)
            for key in totals[sale["sku"]]:
                totals[sale["sku"]][key] += amounts[key]
        result = []
        for sku, product in products.items():
            values = totals[sku]
            result.append({**product, "units_sold": values["units"],
                           "revenue": rubles(values["revenue"]), "profit": rubles(values["profit"]),
                           "margin": margin(values["profit"], values["revenue"])})
        return result

    def build_report(date_from: Date, date_to: Date) -> dict:
        if date_from > date_to:
            raise HTTPException(status_code=400, detail="Начало периода должно быть не позже окончания")
        demo = seed()
        products = {product["sku"]: product for product in demo["products"]}
        totals = dict.fromkeys(("units", "revenue", "commission", "logistics", "cost", "expenses", "profit"), 0)
        daily: dict[str, dict] = {}
        start, end = date_from.isoformat(), date_to.isoformat()
        for sale in demo["sales"]:
            day = sale["date"]
            if start <= day <= end:
                values = sale_amounts(sale, products)
                for key, value in values.items():
                    totals[key] += value
                row = daily.setdefault(day, {"revenue": 0, "profit": 0})
                row["revenue"] += values["revenue"]
                row["profit"] += values["profit"]
        for expense in storage_call(store.read_expenses):
            day = expense["date"]
            if start <= day <= end:
                amount = cents(expense["amount"])
                totals["expenses"] += amount
                totals["profit"] -= amount
                daily.setdefault(day, {"revenue": 0, "profit": 0})["profit"] -= amount
        money_totals = {key: value if key == "units" else rubles(value) for key, value in totals.items()}
        money_totals["margin"] = margin(totals["profit"], totals["revenue"])
        return {"marketplace": "wildberries", "period": {"from": start, "to": end}, "totals": money_totals,
                "daily": [{"date": day, "revenue": rubles(row["revenue"]), "profit": rubles(row["profit"])}
                          for day, row in sorted(daily.items())]}

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(PROJECT / "static" / "index.html")

    @application.get("/health")
    def health() -> dict:
        storage_call(store.read_expenses)
        return {"status": "ok", "marketplace": "wildberries", "shop_name": shop_name, "mode": "demo"}

    @application.get("/version")
    def version() -> dict:
        return {"version": VERSION, "name": "LAITYS Wildberries Study"}

    @application.get("/api/products")
    def products(q: str = Query(default="", max_length=200)) -> dict:
        query = q.strip().casefold()
        items = [item for item in product_items() if query in item["sku"].casefold() or query in item["name"].casefold()]
        return {"items": items, "total": len(items), "marketplace": "wildberries"}

    @application.get("/api/products/{sku}")
    def product(sku: str) -> dict:
        item = next((item for item in product_items() if item["sku"] == sku), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return item

    @application.get("/api/report")
    def report(date_from: Date = PERIOD_FROM, date_to: Date = PERIOD_TO) -> dict:
        return build_report(date_from, date_to)

    @application.get("/api/expenses")
    def expenses() -> dict:
        items = storage_call(store.read_expenses)
        return {"items": items, "total": len(items)}

    @application.post("/api/expenses", status_code=201)
    def add_expense(expense: ExpenseInput) -> dict:
        item = storage_call(store.add_expense, expense)
        logger.info("expense_create result=ok")
        return item

    @application.delete("/api/expenses/{expense_id}", status_code=204)
    def delete_expense(expense_id: str) -> Response:
        if not storage_call(store.delete_expense, expense_id):
            logger.info("expense_delete result=not_found")
            raise HTTPException(status_code=404, detail="Расход не найден")
        logger.info("expense_delete result=ok")
        return Response(status_code=204)

    @application.get("/api/export")
    def export(date_from: Date = PERIOD_FROM, date_to: Date = PERIOD_TO) -> Response:
        report_data = build_report(date_from, date_to)
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        writer.writerow(["date", "revenue", "profit"])
        for row in report_data["daily"]:
            writer.writerow([row["date"], f"{row['revenue']:.2f}", f"{row['profit']:.2f}"])
        filename = f"laitys-wb-{date_from}-{date_to}.csv"
        return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return application


app = create_app()
