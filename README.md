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

## Конфигуратор КП

`public/cfg.html` — черновик спецификации. Стекло: площадь проёма × ставки `GLASS_RATES` в этом файле (оценка, в строке BOM так и подписана). Фурнитура: позиции `GET /api/catalog` (фильтры `group`, `source`, `q`). В сумму попадает поле `price` из ответа. Демо-наборы «базовый / усиленный» удалены.

Закуп в JSON видят admin, owner и accountant — как в ЛК. У остальных ролей `price` пустой: позицию можно добавить в спецификацию, итог она не увеличивает.

Один origin для страницы и cookie (на VDS это уже делает nginx; локально — флаг ниже). `DUSHEVYE_SERVE_PUBLIC` на сервере не включайте.

```bash
export DUSHEVYE_SESSION_SECRET=dev-only-not-for-production
export DUSHEVYE_DB="$PWD/data/app.db"
export DUSHEVYE_CATALOG_DB="$PWD/data/catalog/catalog.db"
export DUSHEVYE_ADMIN_PASS_FILE="$PWD/secrets/initial-admin.txt"
export DUSHEVYE_SERVE_PUBLIC=1
mkdir -p secrets data/catalog
.venv/bin/python -m app.seed
.venv/bin/python -m app
```

Откройте http://127.0.0.1:8088/cfg.html . Без сессии каталог отвечает 401 — кнопка «Войти» возвращает на конфигуратор (`/lk/login.html?next=/cfg.html`). Пароль админа лежит в `secrets/initial-admin.txt`. Дальше: группа (петли, ручки, уплотнители…), поиск, «Добавить в КП». В превью — стекло и фурнитура (артикул, название, количество, цена, сумма) и итог.

## Сохранение КП и PDF

Черновик пишется в ту же SQLite, что и учётки (`DUSHEVYE_DB`, таблицы `quotes` и `quote_items`). Организация и автор берутся из сессии, не из тела запроса. Цена фурнитуры тоже: сервер ещё раз читает каталог и сохраняет снимок (id, количество, артикул, название, закуп). Клиентская цена игнорируется. Стекло сервер считает теми же ставками, что `GLASS_RATES` в `cfg.html`.

`POST /api/quotes` требует заголовок `X-CSRF-Token` (`GET /api/auth/csrf`), как вход.

Кто видит черновик:

| Роль | Список и карточка | Цены закупа в ответе и PDF |
|---|---|---|
| admin, owner | все КП своей организации | да |
| accountant | только свои | да |
| manager и остальные | только свои | нет: в PDF состав BOM, в итоге только оценка стекла |

Чужое или чужой организации КП отвечает 404.

PDF: `GET /api/quotes/{id}/pdf` открывает файл в браузере, `?download=1` отдаёт его как скачивание. Сборка на `fpdf2` (чистый Python, без WeasyPrint). Нужен TTF с кириллицей: на Ubuntu это пакет `fonts-dejavu-core` (DejaVu Sans). Если шрифта нет, PDF отвечает 503, сама запись КП остаётся.

Кнопка «Сохранить КП» в `cfg.html` появляется у расчёта. После сохранения — ссылки «Открыть PDF» и «Скачать PDF» и строка в блоке «Сохранённые КП». «Печать черновика» по-прежнему печатает текущий экран через `window.print()`, без записи в базу.

Как проверить локально (после команд из блока «Конфигуратор КП» выше, сервер с `DUSHEVYE_SERVE_PUBLIC=1`):

1. Откройте http://127.0.0.1:8088/cfg.html , войдите, добавьте позицию из каталога.
2. «Сохранить КП». Внизу страницы строка в списке, ссылки открывают PDF со стеклом и фурнитурой (артикул, количество, сумма).
3. Выйдите и войдите менеджером, которого создал админ. В списке только его КП; в PDF позиции есть, закупа нет. Админ в том же списке видит и своё, и менеджерское, с закупом.
4. `GET /api/catalog` и вход без этих шагов работают как раньше. В журнале админа есть событие `quote_create`.

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

- CSRF на входе, выходе, смене пароля, приглашении, смене роли и сохранении КП (`POST /api/quotes`). Токен: `GET /api/auth/csrf`, заголовок `X-CSRF-Token`.
- Лимит входа: 5 попыток за 15 минут с одного IP и email, дальше `429`.
- Cookie сессии: HttpOnly и `SameSite=Strict`. Флаг Secure включается только при `DUSHEVYE_COOKIE_SECURE=1` (после HTTPS). Пока сайт по HTTP на IP, флаг остаётся выключенным.
- CSP и сопутствующие заголовки в `deploy/nginx-dushevye.conf`. В `script-src` пока есть `'unsafe-inline'` — у страниц ЛК ещё инлайновые скрипты.
- Временный пароль новой учётки показывается в модалке с копированием, не через `alert()`.

HTTPS и домен по-прежнему не делаем. Секрет сессии только из `DUSHEVYE_SESSION_SECRET`.

Подробный разбор и фазы — в `DEVELOPMENT.md`.
