# parsers — комплектующие поставщиков

CLI:

```bash
python -m parsers list
python -m parsers run --source titan
python -m parsers run --all --max-items 50 --max-pages 2 --delay 1.0
```

Выход:

- `data/imports/<source>.csv` — сырой импорт
- `data/imports/all-latest.csv` — всё вместе
- `data/catalog/parts.json` — каталог **по группам**
- `data/catalog/catalog.db` — SQLite seed (`part_groups`, `parts`)
- `data/catalog/import-report.json` — счётчики

Группы: `hinges`, `handles`, `connectors`, `rods`, `seals`, `sliding`, `other`.

Зависимости: `requests`, `beautifulsoup4`, `lxml` (уже в `.venv` проекта).

Умеренный rate limit (по умолчанию 1 с). На VDS крон не ставим без отдельного решения.

## Допарсинг фото и параметров

```bash
python -m parsers enrich --download-images --delay 1.0
```

Читает `data/imports/all-latest.csv`, ходит на карточки товара, пишет
`image_url` / `material` / `finish` / `glass_mm` / `params_json`,
скачивает фото в `public/media/parts/`, пересобирает JSON/SQLite.
