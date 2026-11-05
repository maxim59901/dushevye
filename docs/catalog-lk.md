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

## Обновление
```bash
cd /path/to/dushevye-setup
.venv/bin/python -m parsers enrich --download-images
# затем выкладка payload на VDS (catalog.db + media + lk)
```
