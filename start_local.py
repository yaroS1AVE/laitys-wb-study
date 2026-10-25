"""Локальный запуск сайта; клиент WSL удерживает Ubuntu до остановки Uvicorn."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

import psycopg
from dotenv import load_dotenv
import uvicorn

PROJECT = Path(__file__).resolve().parent


def wait_for_database(database_url: str, wsl_process: subprocess.Popen) -> None:
    deadline = monotonic() + 40
    while monotonic() < deadline:
        if wsl_process.poll() is not None:
            raise RuntimeError("Не удалось запустить PostgreSQL в Ubuntu через WSL")
        try:
            with psycopg.connect(database_url, connect_timeout=2) as connection:
                connection.execute("SELECT 1")
            return
        except psycopg.Error:
            sleep(0.5)
    raise RuntimeError("PostgreSQL не ответила за 40 секунд; проверьте Ubuntu и LAITYS_DATABASE_URL")


def main() -> int:
    parser = argparse.ArgumentParser(description="Запуск LAITYS WB Study")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65535:
        parser.error("port must be between 1 and 65535")

    load_dotenv(PROJECT / ".env")
    database_url = os.getenv("LAITYS_DATABASE_URL", "").strip()
    if not database_url:
        print("Укажите LAITYS_DATABASE_URL в .env перед запуском", file=sys.stderr)
        return 1

    wsl_process = None
    try:
        distribution = os.getenv("LAITYS_WSL_DISTRIBUTION", "").strip()
        if os.name == "nt" and distribution:
            print(f"Запуск Ubuntu/PostgreSQL через WSL ({distribution})...", flush=True)
            wsl_process = subprocess.Popen(
                ["wsl.exe", "--distribution", distribution, "--user", "root", "--",
                 "sh", "-c", "service postgresql start && exec sleep infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            wait_for_database(database_url, wsl_process)
        uvicorn.run("app_final:app", host=arguments.host, port=arguments.port)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError):
        # Ни ошибки libpq, ни строка подключения с паролем не попадают в журнал.
        print("Не удалось подготовить PostgreSQL. Проверьте Ubuntu и настройки .env; см. README.md", file=sys.stderr)
        return 1
    finally:
        if wsl_process is not None and wsl_process.poll() is None:
            # Завершается только запущенный нами клиент; всю Ubuntu не останавливаем.
            wsl_process.terminate()
            try:
                wsl_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                wsl_process.kill()
                wsl_process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
