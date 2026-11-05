"""CLI: python -m parsers run|enrich|list"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parsers import SOURCES
from parsers.base import Http, read_csv, write_csv
from parsers.catalog import group_counts, write_json, write_sqlite
from parsers.classify import GROUP_LABELS
from parsers.enrich import download_images, enrich_all
from parsers.sources import REGISTRY


def run_one(name: str, http: Http, max_items: int, max_pages: int, out_dir: Path):
    mod = REGISTRY[name]
    print(f"==> {name}: {getattr(mod, 'NOTES', '')[:120]}")
    path = out_dir / f"{name}.csv"
    prev = read_csv(path) if path.exists() else []
    try:
        items = mod.parse(http, max_items=max_items, max_pages=max_pages)
    except Exception as e:
        print(f"!! {name} FAILED: {e}")
        if prev:
            print(f"    keep previous {len(prev)} from {path}")
            return prev, {"source": name, "ok": False, "error": str(e), "count": len(prev), "kept_previous": True}
        return [], {"source": name, "ok": False, "error": str(e), "count": 0}
    if not items and prev:
        print(f"    empty result, keep previous {len(prev)} from {path}")
        return prev, {"source": name, "ok": False, "error": "empty_result_kept_previous", "count": len(prev), "kept_previous": True, "file": str(path)}
    n = write_csv(path, items)
    print(f"    {n} позиций -> {path}")
    return items, {"source": name, "ok": True, "count": n, "file": str(path), "notes": getattr(mod, "NOTES", "")}


def write_outputs(all_items, out_dir: Path, catalog_dir: Path, report_extra: dict | None = None):
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    combined = out_dir / f"all-{stamp}.csv"
    write_csv(combined, all_items)
    write_csv(out_dir / "all-latest.csv", all_items)
    write_json(catalog_dir / "parts.json", all_items)
    write_sqlite(catalog_dir / "catalog.db", all_items)
    counts = group_counts(all_items)
    with_img = sum(1 for x in all_items if x.image_url)
    with_mat = sum(1 for x in all_items if x.material)
    with_fin = sum(1 for x in all_items if x.finish)
    with_glass = sum(1 for x in all_items if x.glass_mm)
    with_params = sum(1 for x in all_items if x.params_json)
    summary = {
        "when": stamp,
        "total": len(all_items),
        "with_image": with_img,
        "with_material": with_mat,
        "with_finish": with_fin,
        "with_glass_mm": with_glass,
        "with_params": with_params,
        "by_group": {gid: {"title": GROUP_LABELS[gid], "count": counts.get(gid, 0)} for gid in GROUP_LABELS},
        "files": {
            "csv_combined": str(combined),
            "csv_latest": str(out_dir / "all-latest.csv"),
            "parts_json": str(catalog_dir / "parts.json"),
            "catalog_db": str(catalog_dir / "catalog.db"),
        },
    }
    if report_extra:
        summary.update(report_extra)
    (catalog_dir / "import-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== ИТОГО ===")
    print(f"всего: {len(all_items)}")
    print(f"  с фото: {with_img}, материал: {with_mat}, отделка: {with_fin}, стекло: {with_glass}, params: {with_params}")
    for gid, title in GROUP_LABELS.items():
        print(f"  {title}: {counts.get(gid, 0)}")
    print(f"CSV: {combined}")
    print(f"JSON: {catalog_dir / 'parts.json'}")
    print(f"SQLite: {catalog_dir / 'catalog.db'}")
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="parsers", description="Парсеры комплектующих поставщиков")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Запустить парсер")
    run_p.add_argument("--source", choices=list(SOURCES), help="Один поставщик")
    run_p.add_argument("--all", action="store_true", help="Все поставщики")
    run_p.add_argument("--max-items", type=int, default=50, help="Лимит позиций на источник (пробный прогон)")
    run_p.add_argument("--max-pages", type=int, default=2, help="Страниц/секций на источник")
    run_p.add_argument("--delay", type=float, default=1.0, help="Пауза между HTTP-запросами, сек")
    run_p.add_argument("--out", type=Path, default=ROOT / "data" / "imports", help="Папка CSV")
    run_p.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog", help="Папка JSON/SQLite каталога")

    en_p = sub.add_parser("enrich", help="Допарсить фото/параметры для существующих URL")
    en_p.add_argument("--from-csv", type=Path, default=ROOT / "data" / "imports" / "all-latest.csv")
    en_p.add_argument("--out", type=Path, default=ROOT / "data" / "imports")
    en_p.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog")
    en_p.add_argument("--delay", type=float, default=1.0)
    en_p.add_argument("--limit", type=int, default=0, help="Ограничить N позиций (0 = все)")
    en_p.add_argument("--download-images", action="store_true", help="Скачать фото в media/parts")
    en_p.add_argument(
        "--media-dir",
        type=Path,
        default=ROOT / "public" / "media" / "parts",
        help="Куда класть локальный кэш фото",
    )

    sub.add_parser("list", help="Список источников")

    args = p.parse_args(argv)
    if args.cmd == "list":
        for s in SOURCES:
            mod = REGISTRY[s]
            print(f"{s:12} {getattr(mod, 'START', getattr(mod, 'BASE', ''))}")
            print(f"             {getattr(mod, 'NOTES', '')[:140]}")
        return 0

    if args.cmd == "enrich":
        if not args.from_csv.exists():
            print(f"Нет файла {args.from_csv}", file=sys.stderr)
            return 2
        items = read_csv(args.from_csv)
        if args.limit and args.limit > 0:
            items = items[: args.limit]
        print(f"enrich {len(items)} позиций из {args.from_csv}")
        http = Http(delay=args.delay)
        items = enrich_all(http, items)
        if args.download_images:
            print(f"download images -> {args.media_dir}")
            n = download_images(http, items, args.media_dir)
            print(f"  локальных фото: {n}")
        args.out.mkdir(parents=True, exist_ok=True)
        args.catalog.mkdir(parents=True, exist_ok=True)
        write_outputs(items, args.out, args.catalog, {"mode": "enrich"})
        return 0

    if not args.all and not args.source:
        print("Укажите --source NAME или --all", file=sys.stderr)
        return 2

    names = list(SOURCES) if args.all else [args.source]
    http = Http(delay=args.delay)
    args.out.mkdir(parents=True, exist_ok=True)
    args.catalog.mkdir(parents=True, exist_ok=True)

    all_items = []
    report = []
    for name in names:
        items, meta = run_one(name, http, args.max_items, args.max_pages, args.out)
        all_items.extend(items)
        report.append(meta)

    write_outputs(all_items, args.out, args.catalog, {"by_source": {m["source"]: m for m in report}, "mode": "run"})
    for m in report:
        status = "OK" if m.get("ok") else "FAIL"
        print(f"  [{status}] {m['source']}: {m.get('count', 0)} {m.get('error', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
