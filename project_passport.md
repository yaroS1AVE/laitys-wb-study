# Паспорт LAITYS WB Study

Учебный кабинет продавца Wildberries: сводка демонстрационных продаж, поиск товаров, учёт расходов и экспорт CSV. Данные хранятся в PostgreSQL.

| Поле | Значение |
|---|---|
| Git repository | https://github.com/yaroS1AVE/laitys-wb-study |
| Application OS | Windows с Python 3.11+; также поддерживается Linux |
| Database OS | Ubuntu 26.04 LTS через WSL2 на локальном Windows-компьютере |
| Database version | PostgreSQL 18.6 |
| Hosting type | Локальный Uvicorn и отдельный сервис PostgreSQL в Ubuntu |
| Public IP | Не используется |
| Application port | `8000`, задаётся аргументом `--port` |
| PostgreSQL port | `5432`, доступ через `127.0.0.1` |
| Local URL | http://127.0.0.1:8000/ |
| Public URL | Не настроен |
| Process manager | Приложение запускается через `start_local.py`; PostgreSQL управляется сервисом Ubuntu, launcher удерживает WSL во время работы сайта |
| Reverse proxy | Не используется |
| Application logs | Терминал запуска Uvicorn |
| Database logs | `/var/log/postgresql/` в Ubuntu |
| Health check | http://127.0.0.1:8000/health |
| Version | http://127.0.0.1:8000/version |

## Запуск и остановка

Установка базы и приложения описана в [README](README.md). Сначала в Ubuntu:

```bash
sudo service postgresql start
sudo service postgresql status
```

Затем из папки проекта с активированным Python-окружением:

```bash
python start_local.py
```

На Windows `Запустить.cmd` выполняет ту же команду. При `LAITYS_WSL_DISTRIBUTION=Ubuntu` launcher автоматически запускает сервис PostgreSQL и удерживает Ubuntu во время работы сайта. Остановка приложения — `Ctrl+C`; перезапуск — повтор команды. Перезапуск PostgreSQL в Ubuntu: `sudo service postgresql restart`. Проверка `/health` подтверждает соединение с базой и доступность таблиц.

## Данные и проверка

`LAITYS_DATABASE_URL` задаёт базу `laitys`, `LAITYS_TEST_DATABASE_URL` — отдельную `laitys_test`. Настоящий `.env` с паролем исключён из Git; пример настроек находится в `.env.example`.

Таблицы: `products`, `sales`, `expenses`, `app_meta`. Денежные суммы — целые копейки. Данные примера загружаются из `data/demo.json` один раз. Файл `.runtime/expenses.json`, если он существует, импортируется полностью при первом запуске и сохраняется как исходник. После инициализации API работает с PostgreSQL.

Автоматическая проверка: `python -m pytest -v`. Тесты используют отдельную базу и временные схемы. CI проверяет Python 3.11 и 3.12 на Ubuntu с сервисом PostgreSQL и формирует артефакт приложения после успеха.
