# Душевые / Станок

Производство и продажа душевых ограждений под проём: витрина, конфигуратор чернового КП и личные кабинеты по ролям. Каталог комплектующих собирается парсерами поставщиков и отдаётся API.

**GitHub — источник правды** для кода и документов (`maxim59901/dushevye`). Шар и VDS — рабочие копии, не место, откуда берётся история.

Сайт лежит в `public/` (так его отдаёт nginx: корень `/var/www/dushevye/public`). Раньше `index.html`, `cfg.html` и `lk/` лежали в корне репозитория — это ранний снапшот. Сейчас их место только `public/`. `DEVELOPMENT.md` остаётся в корне.

## Состав

| Путь | Что это |
|---|---|
| `public/` | Витрина (`index.html`), конфигуратор (`cfg.html`), ЛК (`public/lk/`) |
| `app/` | FastAPI: вход, роли, журнал, каталог (`/api/*`) |
| `parsers/` | Импорт прайсов поставщиков в CSV / `parts.json` |
| `data/` | Сырые CSV и JSON каталога. SQLite (`*.db`) в git не входит |
| `docs/` | Роли, auth, парсеры, бэкапы |
| `deploy/` | nginx, systemd, скрипт установки на VDS, выкладка по SSH |
| `DEVELOPMENT.md` | Разбор фронтенда и план. Безопасность P1 ещё не закрыта |

Фото товаров (`public/media/parts/`) в репозиторий не кладём.

## Локальный API

Нужен Python 3.11+. Секрет сессии и базы задаются окружением, не файлом в git.

```bash
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
export DUSHEVYE_SESSION_SECRET=dev-only-not-for-production
export DUSHEVYE_DB="$PWD/data/app.db"
export DUSHEVYE_CATALOG_DB="$PWD/data/catalog/catalog.db"
export DUSHEVYE_ADMIN_PASS_FILE="$PWD/secrets/initial-admin.txt"
mkdir -p secrets data/catalog
.venv/bin/python -m app.seed
.venv/bin/python -m app
```

API слушает `127.0.0.1:8088` (`/api/health`). Стартовый пароль админа скрипт пишет в `secrets/initial-admin.txt` и печатает в консоль один раз — этот файл в `.gitignore`.

Каталог (`/api/catalog`) читает SQLite. Файла `catalog.db` в git нет: его собирает `python -m parsers` (зависимости в `parsers/requirements.txt`) в `data/catalog/catalog.db`. Пока базы нет, каталог отвечает 503, auth при этом работает.

Парсеры:

```bash
.venv/bin/pip install -r parsers/requirements.txt
.venv/bin/python -m parsers list
.venv/bin/python -m parsers run --source titan --max-items 20 --max-pages 1
```

## Секреты

В git не коммитятся: пароль VDS, `DUSHEVYE_SESSION_SECRET`, `api.env`, `.env`, сгенерированные пароли пользователей, `*.db`. Пароль выкладки только из окружения `DUSHEVYE_VDS_PASS` или ключа `DUSHEVYE_VDS_KEY` (`deploy/deploy-vds.py`).

## Безопасность P1 (#2–6)

В коде, без выкладки на VDS и без HTTPS:

- CSRF на входе, выходе, смене пароля, приглашении и смене роли. Токен: `GET /api/auth/csrf`, заголовок `X-CSRF-Token`.
- Лимит входа: 5 попыток за 15 минут с одного IP и email, дальше `429`.
- Cookie сессии: HttpOnly и `SameSite=Strict`. Флаг Secure включается только при `DUSHEVYE_COOKIE_SECURE=1` (после HTTPS). Пока сайт по HTTP на IP, флаг остаётся выключенным.
- CSP и сопутствующие заголовки в `deploy/nginx-dushevye.conf`. В `script-src` пока есть `'unsafe-inline'` — у страниц ЛК ещё инлайновые скрипты.
- Временный пароль новой учётки показывается в модалке с копированием, не через `alert()`.

HTTPS и домен по-прежнему не делаем. Секрет сессии только из `DUSHEVYE_SESSION_SECRET`.

Подробный разбор и фазы — в `DEVELOPMENT.md`.
