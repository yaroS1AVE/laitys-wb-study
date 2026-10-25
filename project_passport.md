# Паспорт LAITYS WB Study

Учебный кабинет продавца Wildberries: сводка демонстрационных продаж, поиск товаров, учёт расходов и экспорт CSV. Для работы не нужны аккаунт продавца и ключ API Wildberries.

| Поле | Значение |
|---|---|
| Git repository | https://github.com/yaroS1AVE/laitys-wb-study |
| Server OS | Локальный запуск на Windows, Linux или macOS с Python 3.11+; CI проверяет Python 3.11 и 3.12 на Ubuntu |
| Hosting type | Локальный процесс Uvicorn |
| Public IP | Не используется в учебной конфигурации |
| Application port | `8000` по умолчанию; задаётся аргументом `--port` |
| Local URL | http://127.0.0.1:8000/ |
| Public URL | Не настроен; сервис открывается локально |
| Process manager | Автоматический менеджер процессов не используется; ручной запуск Uvicorn |
| Reverse proxy | Не используется |
| Log command | Логи отображаются в терминале запуска Uvicorn |
| Restart command | Остановить процесс через `Ctrl+C` и повторить команду запуска ниже |
| Health check | http://127.0.0.1:8000/health |
| Version | http://127.0.0.1:8000/version |

## Запуск и проверка

После установки зависимостей по [README](README.md) выполните:

```bash
python -m uvicorn app_final:app --host 127.0.0.1 --port 8000
```

Откройте `/health`: исправный сервис возвращает `status: ok`. Документация запросов доступна на `/docs`. Автоматические проверки запускаются командой `python -m pytest -v`.

Демонстрационные товары и продажи находятся в `data/demo.json`, добавленные расходы сохраняются в `.runtime/expenses.json`. Путь к расходам задаётся через `LAITYS_DATA_DIR`; каталог с изменяемыми данными и настоящий `.env` исключены из Git. Настройки перечислены в `.env.example` и README.
