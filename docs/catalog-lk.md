# Каталог комплектующих в ЛК

Экран: `/lk/catalog.html` (нужен вход).  
API: `GET /api/catalog`, `GET /api/catalog/{id}` (cookie-сессия).

## Данные
- Источник: парсеры `parsers/` + допарсинг карточек (`python -m parsers enrich --download-images`).
- Файлы: `data/catalog/parts.json`, `data/catalog/catalog.db`, CSV в `data/imports/`.
- На VDS: `/var/www/dushevye/data/catalog.db`, фото в `/var/www/dushevye/public/media/parts/` (`/media/parts/...`).

## Поля позиции
`source`, `sku`, `name`, `price`, `currency`, `url`, `group`, `in_stock`, `glass_mm`, `image_url`, `material`, `finish`, `params`.

## Цены
Закупочные цены видят роли **admin / owner / accountant**.  
Менеджер и остальные получают `price: null` и подпись «Цена закупа скрыта» (своя цена менеджера — позже).

## Фильтры API
`group`, `source`, `q` (имя/артикул/материал/отделка), `limit`, `offset`.

Конфигуратор `public/cfg.html` вызывает тот же список: группа, производитель, поиск. В BOM попадает `price` из ответа. Без сессии каталог отвечает 401. Роли без закупа получают `price: null` — конфигуратор не подставляет вместо этого демо-цену.

Сохранение КП не принимает цену от браузера: `POST /api/quotes` заново читает позицию по id и кладёт снимок в `app.db`. В ответе и PDF закуп виден только admin, owner и accountant. Необязательное поле `project_id` привязывает этот черновик к проекту той же организации, если роль может его менять.

## Совместимость в конфигураторе

`GET /api/catalog/compat?glass_mm=&template_id=` (нужна сессия) отдаёт, какие группы и `part_id` нельзя ставить при этой толщине и этом шаблоне. Конфигуратор серит такие позиции и пишет короткую причину. Спецификация и цена уже выбранной строки не пересчитываются заново из-за фильтра: если позиция стала серой, её видно в списке выбранного, а `POST /api/config/validate` не даёт сохранить КП.

Правила — таблица `catalog_compat_rules` в `app.db` (стартовая матрица «группа × толщина» и свои строки админа или хозяина). `GET/POST /api/catalog/compat/rules` и `DELETE /api/catalog/compat/rules/{id}` — только admin и owner, изменение с `X-CSRF-Token`. Менеджер эти методы получает 403. Страница: `/lk/catalog-rules.html`. Файл `catalog.db` при этом только читается.

## Склад

Остатки организации лежат в `app.db` и ссылаются на `parts.id`. Файл `catalog.db` для склада только читается: приход, расход и резерв не меняют строки поставщиков.

## Обновление
```bash
cd /path/to/dushevye-setup
.venv/bin/python -m parsers enrich --download-images
# затем выкладка payload на VDS (catalog.db + media + lk)
```
