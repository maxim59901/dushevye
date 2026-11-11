"""Quote drafts: glass estimate, catalog hardware snapshot, PDF.

Glass rates and the area formula live only here. The browser asks
POST /api/config/estimate and receives the computed numbers, not this table.
Hardware prices are read from the catalog on the server; the client cannot
submit a price.
"""
from __future__ import annotations

import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .catalog_compat import apply_compat, load_compat_rules
from .config_rules import get_template, needed_dimension_ids, template_label, validate_config

FENCE_LABEL = {
    "corner": "Угловая",
    "straight": "Прямая",
    "niche": "Ниша",
    "sliding": "Раздвижная",
    "accordion": "Гармошка",
    "ushape": "П-образная",
    "trapezoid": "Трапеция",
}
KIND_LABEL = {"clear": "прозрачное", "matte": "матовое"}
DEPTH_TYPES = frozenset({"corner", "niche"})
# Quotes saved before templates. New shapes go through template_id only.
LEGACY_FENCE_TYPES = frozenset({"corner", "straight", "niche", "sliding", "accordion"})

# Server-only. Do not copy this table into public/cfg.html or an API response.
GLASS_RATES = {
    "per_sqm": {8: 4200.0, 10: 5100.0},
    "kind_mult": {"clear": 1.0, "matte": 1.18},
    "type_factor": {
        "corner": 1.12,
        "straight": 1.0,
        "niche": 1.08,
        "sliding": 1.15,
        "accordion": 1.22,
        "ushape": 1.18,
        "trapezoid": 1.1,
    },
}

_FONT_CANDIDATES = (
    (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ),
    (
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ),
)
_font_pair_cache: Optional[tuple[Path, Path]] = None


class QuoteItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    qty: int


class QuoteIn(BaseModel):
    """Client payload. Prices and org/user are ignored — the server fills them."""

    model_config = ConfigDict(extra="ignore")
    fence_type: str = ""
    template_id: str = ""
    width_mm: int
    height_mm: int
    depth_mm: Optional[int] = None
    depth2_mm: Optional[int] = None
    angle_deg: Optional[int] = None
    doors: Optional[int] = None
    glass_thick_mm: int
    glass_kind: str = ""
    items: list[QuoteItemIn] = Field(default_factory=list)
    project_id: Optional[int] = None


def round_money(n: float) -> float:
    """Half-up rounding to kopeks, same as Math.round(n * 100) / 100 for positive n."""
    return math.floor(float(n) * 100 + 0.5) / 100.0


def round_area(n: float) -> float:
    return math.floor(float(n) * 10000 + 0.5) / 10000.0


def area_sqm(
    fence_type: str,
    width_mm: int,
    height_mm: int,
    depth_mm: Optional[int],
    depth2_mm: Optional[int] = None,
) -> float:
    wm = width_mm / 1000.0
    hm = height_mm / 1000.0
    dm = (depth_mm or 0) / 1000.0
    d2 = (depth2_mm or 0) / 1000.0
    if fence_type in ("corner", "trapezoid"):
        return (wm + dm) * hm
    if fence_type == "niche":
        return (wm + 2 * dm) * hm * 0.55
    if fence_type == "accordion":
        return wm * hm * 1.15
    if fence_type == "ushape":
        return (dm + wm + d2) * hm
    return wm * hm


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    return text[:limit]


def _merge_items(items: list[QuoteItemIn]) -> list[dict[str, int]]:
    if len(items) > 80:
        raise HTTPException(status_code=400, detail="Не больше 80 позиций фурнитуры")
    merged: list[dict[str, int]] = []
    index: dict[int, int] = {}
    for item in items:
        if item.id < 1:
            raise HTTPException(status_code=400, detail="Некорректная позиция каталога")
        if item.qty < 1 or item.qty > 9999:
            raise HTTPException(status_code=400, detail="Количество от 1 до 9999")
        if item.id in index:
            slot = merged[index[item.id]]
            slot["qty"] += item.qty
            if slot["qty"] > 9999:
                raise HTTPException(status_code=400, detail="Суммарное количество позиции больше 9999")
        else:
            index[item.id] = len(merged)
            merged.append({"id": item.id, "qty": item.qty})
    return merged


def _glass_kind(raw: str) -> str:
    kind = (raw or "").strip().lower()
    if kind not in KIND_LABEL:
        raise HTTPException(status_code=400, detail="Вид стекла: clear или matte")
    return kind


def normalize_quote(body: QuoteIn) -> dict[str, Any]:
    template_id = (body.template_id or "").strip()
    if template_id:
        return _normalize_templated(body, template_id)
    fence = (body.fence_type or "").strip().lower()
    if fence not in LEGACY_FENCE_TYPES:
        raise HTTPException(status_code=400, detail="Неизвестный тип ограждения")
    if body.width_mm < 400 or body.width_mm > 4000:
        raise HTTPException(status_code=400, detail="Ширина от 400 до 4000 мм")
    if body.height_mm < 400 or body.height_mm > 3000:
        raise HTTPException(status_code=400, detail="Высота от 400 до 3000 мм")
    if body.glass_thick_mm not in GLASS_RATES["per_sqm"]:
        raise HTTPException(status_code=400, detail="Толщина стекла: 8 или 10 мм")
    kind = _glass_kind(body.glass_kind)
    depth = body.depth_mm
    if fence in DEPTH_TYPES:
        if depth is None:
            raise HTTPException(status_code=400, detail="Для угловой и ниши нужна глубина")
        if depth < 400 or depth > 2000:
            raise HTTPException(status_code=400, detail="Глубина от 400 до 2000 мм")
    elif depth is not None and (depth < 400 or depth > 2000):
        raise HTTPException(status_code=400, detail="Глубина от 400 до 2000 мм")
    return {
        "fence_type": fence,
        "template_id": None,
        "width_mm": body.width_mm,
        "height_mm": body.height_mm,
        "depth_mm": depth,
        "depth2_mm": None,
        "angle_deg": None,
        "door_count": None,
        "doors": None,
        "glass_thick_mm": body.glass_thick_mm,
        "glass_kind": kind,
        "items": _merge_items(body.items),
    }


def _normalize_templated(body: QuoteIn, template_id: str) -> dict[str, Any]:
    template = get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "template_id": template_id,
                "errors": [{"code": "template_id", "message": "Неизвестный шаблон конструкции."}],
                "warnings": [],
            },
        )
    kind = _glass_kind(body.glass_kind)
    needed = needed_dimension_ids(template)
    choices = template.get("door_choices") or ()
    door_count = int(body.doors if body.doors is not None else choices[0]) if choices else None
    return {
        "fence_type": template["fence_type"],
        "template_id": template["id"],
        "width_mm": body.width_mm,
        "height_mm": body.height_mm,
        "depth_mm": body.depth_mm if "depth_mm" in needed else None,
        "depth2_mm": body.depth2_mm if "depth2_mm" in needed else None,
        "angle_deg": body.angle_deg if "angle_deg" in needed else None,
        "door_count": door_count,
        "doors": door_count,
        "glass_thick_mm": body.glass_thick_mm,
        "glass_kind": kind,
        "items": _merge_items(body.items),
    }


def validation_error(result: dict[str, Any]) -> HTTPException:
    return HTTPException(status_code=400, detail=result)


def quote_is_valid(
    draft: dict[str, Any],
    parts: list[dict[str, Any]],
    org_id: Optional[int] = None,
) -> dict[str, Any]:
    """Full template check plus catalog compat. Empty parts still warn about missing hinges."""
    result = validate_config(draft, parts)
    result = apply_compat(result, draft, parts, load_compat_rules(org_id))
    if draft.get("template_id") and result["ok"]:
        thick = draft["glass_thick_mm"]
        if thick not in GLASS_RATES["per_sqm"]:
            result = {
                "ok": False,
                "template_id": draft["template_id"],
                "errors": result["errors"]
                + [
                    {
                        "code": "glass_thickness",
                        "message": f"Для толщины {thick} мм нет ставки стекла, сохранить КП нельзя.",
                    }
                ],
                "warnings": result["warnings"],
            }
    return result


def catalog_db_path() -> Path:
    return Path(os.environ.get("DUSHEVYE_CATALOG_DB", "/var/www/dushevye/data/catalog.db"))


def fetch_parts(ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    path = catalog_db_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail="Каталог ещё не загружен")
    placeholders = ",".join("?" * len(ids))
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""SELECT p.id, p.sku, p.name, p.price, p.source, p.group_id, p.glass_mm,
                       g.title_ru AS group_title
                FROM parts p
                LEFT JOIN part_groups g ON g.id = p.group_id
                WHERE p.id IN ({placeholders})""",
            ids,
        ).fetchall()
    finally:
        conn.close()
    found = {int(row["id"]): row for row in rows}
    missing = [part_id for part_id in ids if part_id not in found]
    if missing:
        shown = ", ".join(str(part_id) for part_id in missing[:10])
        raise HTTPException(status_code=400, detail=f"В каталоге нет позиций: {shown}")
    return found


def parts_for_rules(rows: dict[int, sqlite3.Row], items: list[dict[str, int]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in items:
        row = rows[item["id"]]
        keys = row.keys()
        group_id = row["group_id"] if "group_id" in keys else ""
        glass = row["glass_mm"] if "glass_mm" in keys else ""
        title = row["group_title"] if "group_title" in keys else ""
        prepared.append(
            {
                "id": int(row["id"]),
                "sku": row["sku"] or "",
                "name": row["name"] or "",
                "group_id": group_id or "",
                "group_title": title or "",
                "glass_mm": glass or "",
                "qty": int(item["qty"]),
            }
        )
    return prepared


def compose_quote(draft: dict[str, Any], parts: dict[int, sqlite3.Row]) -> dict[str, Any]:
    area = area_sqm(
        draft["fence_type"],
        draft["width_mm"],
        draft["height_mm"],
        draft.get("depth_mm"),
        draft.get("depth2_mm"),
    )
    unit = (
        GLASS_RATES["per_sqm"][draft["glass_thick_mm"]]
        * GLASS_RATES["kind_mult"][draft["glass_kind"]]
        * GLASS_RATES["type_factor"][draft["fence_type"]]
    )
    glass_sum = round_money(area * unit)
    kind_label = KIND_LABEL[draft["glass_kind"]]
    thick = draft["glass_thick_mm"]
    lines: list[dict[str, Any]] = [
        {
            "line_no": 0,
            "kind": "glass",
            "part_id": None,
            "sku": "",
            "name": f"Стекло {thick} мм, {kind_label} · оценка",
            "qty": area,
            "unit": "м²",
            "unit_price": unit,
            "line_sum": glass_sum,
            "source": "",
            "group_title": "",
        }
    ]
    for offset, item in enumerate(draft["items"], start=1):
        part = parts[item["id"]]
        raw_price = part["price"]
        unit_price = None if raw_price is None else float(raw_price)
        line_sum = None if unit_price is None else round_money(unit_price * item["qty"])
        lines.append(
            {
                "line_no": offset,
                "kind": "hardware",
                "part_id": int(part["id"]),
                "sku": _clip(part["sku"], 80),
                "name": _clip(part["name"], 400) or "Позиция",
                "qty": item["qty"],
                "unit": "шт",
                "unit_price": unit_price,
                "line_sum": line_sum,
                "source": _clip(part["source"], 40),
                "group_title": _clip(part["group_title"], 120),
            }
        )
    hardware_sum = round_money(
        sum(line["line_sum"] for line in lines if line["kind"] == "hardware" and line["line_sum"] is not None)
    )
    return {
        "lines": lines,
        "glass_area_m2": area,
        "glass_unit_price": unit,
        "glass_sum": glass_sum,
        "hardware_sum": hardware_sum,
        "total": round_money(glass_sum + hardware_sum),
    }


def _cell(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if key in row.keys():
            return row[key]
    except (AttributeError, IndexError):
        return default
    return default


def _money_out(value: Any, *, visible: bool) -> Optional[float]:
    if not visible or value is None:
        return None
    return round_money(float(value))


def public_estimate(built: dict[str, Any], *, show_price: bool) -> dict[str, Any]:
    """Computed quote figures only. Rate tables and area coefficients stay here."""
    lines_out: list[dict[str, Any]] = []
    for line in built["lines"]:
        hardware = line["kind"] == "hardware"
        visible = show_price or not hardware
        qty_raw = float(line["qty"] or 0)
        if line["kind"] == "glass":
            qty_out: Any = round_area(qty_raw)
        elif abs(qty_raw - round(qty_raw)) < 1e-6:
            qty_out = int(round(qty_raw))
        else:
            qty_out = qty_raw
        lines_out.append(
            {
                "kind": line["kind"],
                "part_id": line["part_id"],
                "sku": line["sku"] or None,
                "name": line["name"],
                "qty": qty_out,
                "unit": line["unit"],
                "unit_price": _money_out(line["unit_price"], visible=visible),
                "line_sum": _money_out(line["line_sum"], visible=visible),
            }
        )
    glass_sum = round_money(built["glass_sum"])
    return {
        "ok": True,
        "glass_area_m2": round_area(built["glass_area_m2"]),
        "glass_unit_price": round_money(built["glass_unit_price"]),
        "glass_sum": glass_sum,
        "hardware_sum": _money_out(built["hardware_sum"], visible=show_price),
        "total": round_money(built["total"]) if show_price else glass_sum,
        "show_price": show_price,
        "lines": lines_out,
    }


def present_quote(
    quote: Any,
    items: list[Any],
    *,
    show_price: bool,
    include_lines: bool,
    allow_pdf: bool = True,
) -> dict[str, Any]:
    item_list = list(items or [])
    if item_list:
        hardware_count = sum(1 for item in item_list if _cell(item, "kind") == "hardware")
    else:
        hardware_count = int(_cell(quote, "hardware_count", 0) or 0)
    lines_out: list[dict[str, Any]] = []
    if include_lines:
        for item in item_list:
            kind = _cell(item, "kind")
            hardware = kind == "hardware"
            visible = show_price or not hardware
            sku = _cell(item, "sku") or ""
            qty_raw = float(_cell(item, "qty") or 0)
            if kind == "glass":
                qty_out: Any = round_area(qty_raw)
            elif abs(qty_raw - round(qty_raw)) < 1e-6:
                qty_out = int(round(qty_raw))
            else:
                qty_out = qty_raw
            lines_out.append(
                {
                    "kind": kind,
                    "part_id": _cell(item, "part_id"),
                    "sku": sku or None,
                    "name": _cell(item, "name") or "",
                    "group_title": _cell(item, "group_title") or "",
                    "source": _cell(item, "source") or "",
                    "qty": qty_out,
                    "unit": _cell(item, "unit") or "",
                    "unit_price": _money_out(_cell(item, "unit_price"), visible=visible),
                    "line_sum": _money_out(_cell(item, "line_sum"), visible=visible),
                }
            )
    quote_id = int(_cell(quote, "id"))
    fence = _cell(quote, "fence_type") or ""
    template_id = _cell(quote, "template_id") or ""
    kind = _cell(quote, "glass_kind") or ""
    glass_sum = round_money(float(_cell(quote, "glass_sum") or 0))
    total = round_money(float(_cell(quote, "total") or 0)) if show_price else glass_sum
    payload: dict[str, Any] = {
        "id": quote_id,
        "status": _cell(quote, "status") or "draft",
        "fence_type": fence,
        "fence_label": template_label(template_id) or FENCE_LABEL.get(fence, fence),
        "template_id": template_id or None,
        "width_mm": _cell(quote, "width_mm"),
        "height_mm": _cell(quote, "height_mm"),
        "depth_mm": _cell(quote, "depth_mm"),
        "depth2_mm": _cell(quote, "depth2_mm"),
        "angle_deg": _cell(quote, "angle_deg"),
        "door_count": _cell(quote, "door_count"),
        "glass_thick_mm": _cell(quote, "glass_thick_mm"),
        "glass_kind": kind,
        "glass_label": KIND_LABEL.get(kind, kind),
        "glass_area_m2": round_area(float(_cell(quote, "glass_area_m2") or 0)),
        "glass_unit_price": _money_out(_cell(quote, "glass_unit_price"), visible=True),
        "glass_sum": glass_sum,
        "hardware_count": hardware_count,
        "hardware_sum": _money_out(_cell(quote, "hardware_sum"), visible=show_price),
        "total": total,
        "totals_basis": "catalog" if show_price else "glass",
        "show_price": show_price,
        "currency": _cell(quote, "currency") or "RUB",
        "user_id": _cell(quote, "user_id"),
        "author_name": _cell(quote, "author_name") or "",
        "author_email": _cell(quote, "author_email") or "",
        "created_at": _cell(quote, "created_at"),
        "pdf_url": f"/api/quotes/{quote_id}/pdf" if allow_pdf else None,
    }
    if include_lines:
        payload["lines"] = lines_out
    return payload


def _format_qty(value: Any, unit: str) -> str:
    number = float(value or 0)
    if abs(number - round(number)) < 1e-6:
        text = str(int(round(number)))
    else:
        text = f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{text} {unit}".strip()


def _format_money(value: Any) -> str:
    if value is None:
        return "—"
    number = round_money(float(value))
    sign = "−" if number < 0 else ""
    number = abs(number)
    whole = int(math.floor(number + 1e-9))
    frac = int(math.floor((number - whole) * 100 + 0.5))
    if frac == 100:
        whole += 1
        frac = 0
    grouped = f"{whole:,}".replace(",", " ")
    if frac:
        return f"{sign}{grouped},{frac:02d} ₽"
    return f"{sign}{grouped} ₽"


def _size_text(quote: dict[str, Any]) -> str:
    if quote.get("fence_type") == "ushape" and quote.get("depth_mm") and quote.get("depth2_mm"):
        text = (
            f"{quote['depth_mm']} × {quote['width_mm']} × {quote['depth2_mm']} × {quote['height_mm']} мм"
        )
    elif quote.get("depth_mm"):
        text = f"{quote['width_mm']} × {quote['height_mm']} × {quote['depth_mm']} мм"
    else:
        text = f"{quote['width_mm']} × {quote['height_mm']} мм"
    if quote.get("angle_deg"):
        text += f", угол {quote['angle_deg']}°"
    if quote.get("door_count") == 2:
        text += ", две створки"
    return text


def _font_pair() -> tuple[Path, Path]:
    global _font_pair_cache
    if _font_pair_cache and _font_pair_cache[0].is_file():
        return _font_pair_cache
    for regular, bold in _FONT_CANDIDATES:
        if regular.is_file():
            pair = (regular, bold if bold.is_file() else regular)
            _font_pair_cache = pair
            return pair
    raise FileNotFoundError("cyrillic-font")


def render_quote_pdf(quote: dict[str, Any]) -> bytes:
    """Build a one-document PDF. Raises FileNotFoundError when no Cyrillic TTF exists."""
    from fpdf import FPDF

    regular, bold = _font_pair()
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(12, 12, 12)
    pdf.add_font("DejaVu", "", str(regular))
    pdf.add_font("DejaVu", "B", str(bold))
    pdf.set_title(f"КП №{quote['id']}")
    pdf.set_creator("Dushevye")
    pdf.add_page()

    pdf.set_font("DejaVu", "B", 11)
    pdf.set_text_color(196, 90, 44)
    pdf.cell(0, 6, "ДУШЕВЫЕ · СТАНОК", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 9, f"Коммерческое предложение № {quote['id']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.set_text_color(80, 86, 92)
    author = quote.get("author_name") or quote.get("author_email") or ""
    created = quote.get("created_at") or ""
    pdf.cell(0, 6, f"Черновик · {created} · {author}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    for line in (
        f"Тип: {quote['fence_label']}",
        f"Проём: {_size_text(quote)}",
        f"Стекло: {quote['glass_thick_mm']} мм, {quote['glass_label']}",
        f"Площадь (оценка): {_format_qty(quote['glass_area_m2'], 'м²')}",
    ):
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("DejaVu", "B", 12)
    pdf.cell(0, 7, "Спецификация", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)

    rows = [["Артикул", "Позиция", "Кол-во", "Цена", "Сумма"]]
    for line in quote.get("lines") or []:
        if line["kind"] == "glass" and line.get("unit_price") is not None:
            price = _format_money(line["unit_price"]) + "/м²"
        else:
            price = _format_money(line.get("unit_price"))
        name = line.get("name") or ""
        group = line.get("group_title") or ""
        if line["kind"] == "hardware" and group:
            name = f"{group} · {name}"
        rows.append(
            [
                line.get("sku") or "—",
                name,
                _format_qty(line.get("qty"), line.get("unit") or ""),
                price,
                _format_money(line.get("line_sum")),
            ]
        )
    with pdf.table(
        rows=rows,
        col_widths=(16, 44, 13, 14, 13),
        text_align=("LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"),
        line_height=5,
        padding=1.3,
        width=186,
    ):
        pass

    pdf.ln(3)
    pdf.set_font("DejaVu", "B", 12)
    total_label = "Итого" if quote.get("show_price") else "Итого (оценка стекла)"
    pdf.cell(0, 8, f"{total_label}: {_format_money(quote.get('total'))}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(70, 76, 82)
    note = (
        "Стекло посчитано по оценочной ставке (площадь × коэффициент). "
        "Это не оферта. Финальный расчёт — после замера."
    )
    if quote.get("show_price"):
        note += " Цены фурнитуры — закуп из каталога на момент сохранения. Сборка и доставка не входят."
    else:
        note += " Цены закупа фурнитуры для этой роли скрыты и в итог не входят. Состав позиций указан."
    pdf.multi_cell(0, 4.2, note)
    return bytes(pdf.output())
