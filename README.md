# LAITYS WB Study

Кабинет продавца Wildberries на Python, FastAPI и PostgreSQL: сводка продаж, поиск товаров, учёт расходов и экспорт CSV.

Данные вымышленные, за **1–14 октября 2026 года**. Интерфейс обращается к HTTP API приложения, а FastAPI читает товары, продажи и расходы из PostgreSQL.

Адреса, порты и команды управления собраны в [паспорте проекта](project_passport.md).

## PostgreSQL в Ubuntu

На Windows используйте Ubuntu через WSL2. В PowerShell **от имени администратора**:

```powershell
wsl --install -d Ubuntu
```

Если Windows попросит перезагрузку, выполните её и откройте Ubuntu. При первом запуске создайте пользователя Linux. В терминале Ubuntu:

```bash
sudo apt update
sudo apt install -y postgresql
sudo service postgresql start
sudo -u postgres createuser --pwprompt laitys
sudo -u postgres createdb --owner=laitys laitys
sudo -u postgres createdb --owner=laitys laitys_test
```

Задайте пользователю `laitys` пароль. Команды создания пользователя и баз выполняются один раз. База `laitys` хранит данные кабинета, `laitys_test` предназначена для тестов.

## Приложение

Нужны Python 3.11+ и Git:

```bash
git clone https://github.com/yaroS1AVE/laitys-wb-study.git
cd laitys-wb-study
python -m venv .venv
```

Активируйте окружение: Windows PowerShell — `.\.venv\Scripts\Activate.ps1`, Linux — `source .venv/bin/activate`. Если PowerShell запрещает активацию, используйте `.\.venv\Scripts\python.exe` вместо `python`.

```bash
python -m pip install -r requirements-dev.txt
```

Скопируйте `.env.example` в `.env` и замените `CHANGE_ME` своим паролем PostgreSQL в обеих строках подключения. Специальные символы пароля в URL должны быть закодированы. Настоящий `.env` исключён из Git.

```bash
python start_local.py
```

- [Кабинет](http://127.0.0.1:8000/)
- [Документация API](http://127.0.0.1:8000/docs)
- [Проверка приложения и PostgreSQL](http://127.0.0.1:8000/health)
- [Версия](http://127.0.0.1:8000/version)

В Windows также доступен `Запустить.cmd`. При `LAITYS_WSL_DISTRIBUTION=Ubuntu` запуск автоматически поднимает PostgreSQL в Ubuntu и удерживает WSL, пока работает сайт. Для другого расположения базы оставьте эту настройку пустой и запустите PostgreSQL заранее. Остановка приложения — **Ctrl+C**.

## Хранение данных

В PostgreSQL находятся таблицы `products`, `sales`, `expenses` и служебная `app_meta`. Денежные суммы хранятся целым числом копеек; продажи связаны с товарами внешним ключом.

При первом запуске приложение создаёт таблицы и загружает пример из `data/demo.json`. При наличии файла `.runtime/expenses.json` импортируется его полный список расходов, включая пустой. Исходный файл сохраняется. Повторные запуски используют базу; удалённые расходы не возвращаются.

## Тесты и CI

```bash
python -m pytest -v
```

Задайте `LAITYS_TEST_DATABASE_URL` для отдельной базы `laitys_test`. При использовании WSL оставьте сайт запущенным либо откройте терминал Ubuntu на время тестов: это удерживает базу доступной. Тесты выполняют настоящие SQL-запросы, создают временные схемы `test_…` и удаляют их после проверки. Проверяются API, финансовые расчёты, миграция расходов, сохранение после перезапуска, ограничения базы и параллельные записи.

[GitHub Actions](https://github.com/yaroS1AVE/laitys-wb-study/actions) запускает проверки на Python 3.11 и 3.12 с отдельным сервисом PostgreSQL. После успешной проверки создаётся артефакт с приложением.

## Настройки

Переменные окружения имеют приоритет над `.env`. После изменения настроек перезапустите приложение.

| Переменная | Назначение |
|---|---|
| `LAITYS_DATABASE_URL` | Обязательная строка подключения к базе кабинета |
| `LAITYS_TEST_DATABASE_URL` | Отдельная база для тестов |
| `LAITYS_WSL_DISTRIBUTION` | Имя Ubuntu в WSL для автоматического запуска базы на Windows; пустое значение отключает эту функцию |
| `LAITYS_SHOP_NAME` | Название магазина |
| `LAITYS_LOG_LEVEL` | Уровень логов, по умолчанию `INFO` |
| `LAITYS_DATA_DIR` | Папка `expenses.json` для однократного импорта; по умолчанию `.runtime` |

## API и расчёты

| Маршрут | Назначение |
|---|---|
| `GET /api/products?q=лампа` | Каталог и поиск |
| `GET /api/products/{sku}` | Карточка товара |
| `GET /api/report?date_from=2026-10-01&date_to=2026-10-14` | Сводка за период |
| `GET /api/expenses`, `POST /api/expenses` | Просмотр и добавление расходов |
| `DELETE /api/expenses/{id}` | Удаление расхода |
| `GET /api/export` | CSV; поддерживает параметры периода |

Прибыль = выручка − комиссия − логистика − себестоимость − дополнительные расходы. В примере комиссия — 20%, логистика — 35 ₽ за единицу. Прибыль товаров показана до общих расходов; в сводке они вычитаются один раз.

## Диагностика

Логи приложения выводятся в терминал. `/health` возвращает `status: ok` и `database: postgresql`, когда соединение и таблицы доступны.

- Нет соединения с базой — проверьте `.env` и запустите PostgreSQL в Ubuntu: `sudo service postgresql start`.
- Статус PostgreSQL: `sudo service postgresql status`; журнал: `/var/log/postgresql/` в Ubuntu.
- Ответ `503` — приложение не может прочитать или изменить данные; проверьте доступность базы и журнал приложения.
- Порт занят — остановите другой сервер или укажите `--port 8001`.
- Пустая сводка — выберите 1–14 октября 2026 года.
