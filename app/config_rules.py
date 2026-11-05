"""Shower templates and server-side configuration rules.

Templates are a code seed (not catalog.db). Rules look at template limits,
glass thickness, and selected catalog rows (part id, group, sku, name).
They do not write supplier data.

Add a thickness or pair rule by appending a dict to THICKNESS_RULES or
PAIR_RULES. part_ids pins a rule to catalog ids; group_id plus name/sku
fragments covers a series without editing the catalog.
"""
from __future__ import annotations

from typing import Any, Optional

# First matching thickness rule wins for a part. Order is meaningful:
# a more specific fragment ("4-6") should stay above a wider one ("8-10")
# only when their `none` lists do not already separate them.
THICKNESS_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "hinge_4_6",
        "group_id": "hinges",
        "any": ("4-6", "4/6", "6 мм", "6мм"),
        "none": ("8-10", "8/10", "8–10"),
        "thickness_mm": (6,),
        "text": "рассчитана на стекло 4–6 мм",
    },
    {
        "id": "hinge_8_10",
        "group_id": "hinges",
        "any": ("8-10", "8/10", "8–10"),
        "none": ("4-6", "4/6"),
        "thickness_mm": (8, 10),
        "text": "рассчитана на стекло 8 или 10 мм",
    },
    {
        "id": "hinge_10_only",
        "group_id": "hinges",
        "any": ("только 10", "hx10"),
        "none": ("8-10", "8/10", "4-6", "4/6"),
        "thickness_mm": (10,),
        "text": "ставится только на стекло 10 мм",
    },
    {
        "id": "clamp_8_10",
        "group_id": "connectors",
        "any": ("8-10", "8/10", "8–10", "зажим 8"),
        "none": ("4-6", "4/6", "6 мм", "6мм"),
        "thickness_mm": (8, 10),
        "text": "рассчитан на стекло 8 или 10 мм",
    },
    {
        "id": "clamp_6_only",
        "group_id": "connectors",
        "any": ("4-6", "4/6", "зажим 6"),
        "none": ("8-10", "8/10", "8–10"),
        "thickness_mm": (6,),
        "text": "рассчитан на стекло 4–6 мм",
    },
    {
        "id": "seal_8_only",
        "group_id": "seals",
        "any": ("уплотнитель 8", "seal 8"),
        "none": ("8-10", "8/10", "10 мм", "10мм", "6 мм", "6мм"),
        "thickness_mm": (8,),
        "text": "рассчитан на стекло 8 мм",
    },
    {
        "id": "seal_10_only",
        "group_id": "seals",
        "any": ("уплотнитель 10", "seal 10"),
        "none": ("8-10", "8/10", "8 мм", "8мм", "6 мм", "6мм"),
        "thickness_mm": (10,),
        "text": "рассчитан на стекло 10 мм",
    },
    {
        "id": "profile_8_10",
        "group_id": "rods",
        "any": ("8-10", "8/10", "8–10"),
        "none": ("4-6", "4/6"),
        "thickness_mm": (8, 10),
        "text": "рассчитан на стекло 8 или 10 мм",
    },
)

PAIR_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "sliding_with_hinges",
        "groups": ("sliding", "hinges"),
        "message": "Раздвижная фурнитура не сочетается с распашными петлями в одном комплекте.",
    },
    {
        "id": "sliding_with_clamps",
        "groups": ("sliding", "connectors"),
        "message": "Раздвижная система не ставится вместе с зажимами и коннекторами.",
    },
)

# Swing templates: a sliding kit is the wrong family.
SLIDING_BLOCKED = frozenset({"corner_two", "ushape", "trapezoid"})

# Thresholds, not a load calculation. First matching row wins.
# min_hinges is per door leaf.
HINGE_STEPS: tuple[dict[str, int], ...] = (
    {"max_height": 2000, "max_leaf": 800, "min_hinges": 2},
    {"max_height": 2100, "max_leaf": 900, "min_hinges": 2},
    {"max_height": 2400, "max_leaf": 1600, "min_hinges": 3},
)

# min_clamps is per fixed panel.
CLAMP_STEPS: tuple[dict[str, int], ...] = (
    {"max_height": 2000, "min_clamps": 2},
    {"max_height": 2400, "min_clamps": 3},
)


def _dim(
    dim_id: str,
    label: str,
    min_v: int,
    max_v: int,
    *,
    hint: str = "",
    unit: str = "мм",
    not_equal: Optional[int] = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": dim_id,
        "label": label,
        "required": True,
        "min": min_v,
        "max": max_v,
        "unit": unit,
    }
    if hint:
        item["hint"] = hint
    if not_equal is not None:
        item["not_equal"] = not_equal
    return item


TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "corner_two",
        "name": "Угловая (две стенки)",
        "summary": "Две стенки под 90°: глухая панель и распашная дверь.",
        "fence_type": "corner",
        "angle_ne_90": False,
        "glass_thickness_mm": (8, 10),
        "slots": (
            {"id": "panel", "kind": "panel", "label": "Глухая стенка", "required": True},
            {"id": "door", "kind": "door", "label": "Дверь", "required": True, "count": 1},
        ),
        "dimensions": (
            _dim("width_mm", "Ширина A", 700, 1200, hint="Сторона с дверью"),
            _dim("depth_mm", "Глубина B", 700, 1200, hint="Глухая стенка"),
            _dim("height_mm", "Высота", 1800, 2200),
        ),
    },
    {
        "id": "niche_straight",
        "name": "Прямая ниша",
        "summary": "Проём между двумя стенами: одна створка или две.",
        "fence_type": "straight",
        "angle_ne_90": False,
        "glass_thickness_mm": (8, 10),
        "door_choices": (1, 2),
        "two_door_min_width": 1100,
        "slots": (
            {"id": "door", "kind": "door", "label": "Створка", "required": True},
        ),
        "dimensions": (
            _dim("width_mm", "Ширина проёма", 600, 1800),
            _dim("height_mm", "Высота", 1800, 2200),
        ),
    },
    {
        "id": "ushape",
        "name": "П-образная",
        "summary": "Две боковые стенки и фасад с дверью.",
        "fence_type": "ushape",
        "angle_ne_90": False,
        "glass_thickness_mm": (8, 10),
        "slots": (
            {"id": "left", "kind": "panel", "label": "Левая стенка", "required": True},
            {"id": "door", "kind": "door", "label": "Дверь", "required": True, "count": 1},
            {"id": "right", "kind": "panel", "label": "Правая стенка", "required": True},
        ),
        "dimensions": (
            _dim("depth_mm", "Бок B", 700, 1200, hint="Левая стенка"),
            _dim("width_mm", "Фасад A", 800, 1600, hint="Сторона с дверью"),
            _dim("depth2_mm", "Бок C", 700, 1200, hint="Правая стенка"),
            _dim("height_mm", "Высота", 1800, 2200),
        ),
    },
    {
        "id": "trapezoid",
        "name": "Трапеция (непрямой угол)",
        "summary": "Стенка и дверь в проёме, где угол не 90°.",
        "fence_type": "trapezoid",
        "angle_ne_90": True,
        "glass_thickness_mm": (8, 10),
        "slots": (
            {"id": "panel", "kind": "panel", "label": "Стенка", "required": True},
            {"id": "door", "kind": "door", "label": "Дверь", "required": True, "count": 1},
        ),
        "dimensions": (
            _dim("width_mm", "Ширина A", 700, 1400, hint="Сторона с дверью"),
            _dim("depth_mm", "Глубина B", 700, 1400),
            _dim("height_mm", "Высота", 1800, 2200),
            _dim("angle_deg", "Угол", 75, 150, unit="°", not_equal=90),
        ),
    },
)

_BY_ID = {item["id"]: item for item in TEMPLATES}


def list_templates() -> list[dict[str, Any]]:
    return [_public_template(item) for item in TEMPLATES]


def get_template(template_id: str) -> Optional[dict[str, Any]]:
    return _BY_ID.get((template_id or "").strip())


def template_label(template_id: Optional[str]) -> str:
    item = get_template(template_id or "")
    return item["name"] if item else ""


def rule_ids() -> list[str]:
    """Stable ids for tests and for anyone adding the next rule."""
    structural = [
        "dimensions",
        "glass_thickness",
        "two_doors_width",
        "sliding_template",
        "hinge_right_angle",
        "hinge_135",
        "hinges_count",
        "clamps_count",
    ]
    return [rule["id"] for rule in THICKNESS_RULES] + [rule["id"] for rule in PAIR_RULES] + structural


def merge_selected_parts(items: list[Any], part_ids: list[int]) -> tuple[list[dict[str, int]], list[dict[str, str]]]:
    """Fold items [{id, qty}] and part_ids (each counts as 1) into one list."""
    errors: list[dict[str, str]] = []
    merged: dict[int, int] = {}
    order: list[int] = []

    def add(part_id: int, qty: int) -> None:
        if part_id < 1:
            errors.append({"code": "part_id", "message": "Некорректная позиция каталога."})
            return
        if qty < 1 or qty > 9999:
            errors.append({"code": "qty", "message": "Количество фурнитуры от 1 до 9999."})
            return
        if part_id not in merged:
            order.append(part_id)
            merged[part_id] = 0
        merged[part_id] += qty
        if merged[part_id] > 9999:
            errors.append({"code": "qty", "message": "Суммарное количество позиции больше 9999."})

    for raw in part_ids or []:
        add(int(raw), 1)
    for item in items or []:
        if isinstance(item, dict):
            add(int(item.get("id") or 0), int(item.get("qty") or 0))
        else:
            add(int(item.id), int(item.qty))
    return [{"id": part_id, "qty": merged[part_id]} for part_id in order], errors


def validate_config(payload: dict[str, Any], parts: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Return ok, errors, warnings. parts=None skips hardware rules (geometry only)."""
    template_id = str(payload.get("template_id") or "").strip()
    template = get_template(template_id)
    if template is None:
        message = "Выберите шаблон конструкции." if not template_id else "Неизвестный шаблон конструкции."
        return _result(template_id, [{"code": "template_id", "message": message}], [])

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    errors.extend(_dimension_errors(template, payload))
    errors.extend(_glass_errors(template, payload))
    errors.extend(_door_errors(template, payload))
    if parts is not None and not any(item["code"] == "width_mm" or item["code"] == "height_mm" for item in errors):
        errors.extend(_hardware_errors(template, payload, parts))
        warnings.extend(_hardware_warnings(template, payload, parts))
    elif parts is not None:
        # Size is already invalid; still report hardware clashes so both show at once.
        errors.extend(_pair_errors(template, parts))
        errors.extend(_thickness_errors(payload, parts))
        errors.extend(_angle_hardware_errors(template, payload, parts))
    return _result(template_id, errors, warnings)


def needed_dimension_ids(template: dict[str, Any]) -> frozenset[str]:
    return frozenset(dim["id"] for dim in template["dimensions"])


def _public_template(item: dict[str, Any]) -> dict[str, Any]:
    panels = sum(1 for slot in item["slots"] if slot["kind"] == "panel")
    return {
        "id": item["id"],
        "name": item["name"],
        "summary": item["summary"],
        "fence_type": item["fence_type"],
        "angle_ne_90": bool(item["angle_ne_90"]),
        "glass_thickness_mm": list(item["glass_thickness_mm"]),
        "slots": [dict(slot) for slot in item["slots"]],
        "dimensions": [dict(dim) for dim in item["dimensions"]],
        "door_choices": list(item.get("door_choices") or []),
        "two_door_min_width": item.get("two_door_min_width"),
        "panel_count": panels,
    }


def _result(template_id: str, errors: list[dict[str, str]], warnings: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "ok": not errors,
        "template_id": template_id or None,
        "errors": errors,
        "warnings": warnings,
    }


def _dimension_errors(template: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for dim in template["dimensions"]:
        raw = payload.get(dim["id"])
        unit = dim.get("unit") or "мм"
        label = dim["label"]
        if raw is None or raw == "":
            errors.append({"code": dim["id"], "message": f"Укажите «{label}»."})
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
            errors.append({"code": dim["id"], "message": f"{label}: укажите целое число."})
            continue
        value = int(raw)
        if value < int(dim["min"]) or value > int(dim["max"]):
            errors.append(
                {
                    "code": dim["id"],
                    "message": (
                        f"{label}: {value} {unit} вне допуска шаблона "
                        f"({dim['min']}–{dim['max']} {unit})."
                    ),
                }
            )
            continue
        not_equal = dim.get("not_equal")
        if not_equal is not None and value == int(not_equal):
            errors.append(
                {
                    "code": dim["id"],
                    "message": (
                        f"{label}: {value}{unit} не подходит. Для шаблона «{template['name']}» "
                        f"нужен угол, отличный от {not_equal}{unit}."
                    ),
                }
            )
    return errors


def _glass_errors(template: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("glass_thick_mm")
    if raw is None or raw == "":
        return [{"code": "glass_thick_mm", "message": "Укажите толщину стекла."}]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
        return [{"code": "glass_thick_mm", "message": "Толщина стекла: укажите целое число миллиметров."}]
    thick = int(raw)
    allowed = tuple(int(value) for value in template["glass_thickness_mm"])
    if thick not in allowed:
        listed = ", ".join(str(value) for value in allowed)
        return [
            {
                "code": "glass_thickness",
                "message": (
                    f"Толщина стекла {thick} мм не допускается для шаблона «{template['name']}». "
                    f"Допустимо: {listed} мм."
                ),
            }
        ]
    return []


def _door_errors(template: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, str]]:
    choices = tuple(int(value) for value in (template.get("door_choices") or ()))
    errors: list[dict[str, str]] = []
    doors = _door_count(template, payload)
    if choices and doors not in choices:
        listed = " или ".join(str(value) for value in choices)
        errors.append(
            {
                "code": "doors",
                "message": f"Число створок для шаблона «{template['name']}»: {listed}.",
            }
        )
    min_width = template.get("two_door_min_width")
    width = payload.get("width_mm")
    if (
        min_width
        and doors == 2
        and isinstance(width, (int, float))
        and not isinstance(width, bool)
        and int(width) < int(min_width)
    ):
        errors.append(
            {
                "code": "two_doors_width",
                "message": (
                    f"Для двух створок ширина проёма от {int(min_width)} мм, сейчас {int(width)} мм."
                ),
            }
        )
    return errors


def _hardware_errors(
    template: dict[str, Any], payload: dict[str, Any], parts: list[dict[str, Any]]
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    errors.extend(_pair_errors(template, parts))
    errors.extend(_thickness_errors(payload, parts))
    errors.extend(_angle_hardware_errors(template, payload, parts))
    errors.extend(_hinge_count_findings(template, payload, parts, as_error=True))
    return errors


def _hardware_warnings(
    template: dict[str, Any], payload: dict[str, Any], parts: list[dict[str, Any]]
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    warnings.extend(_hinge_count_findings(template, payload, parts, as_error=False))
    warnings.extend(_clamp_warnings(template, payload, parts))
    return warnings


def _pair_errors(template: dict[str, Any], parts: list[dict[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    groups = {str(part.get("group_id") or "") for part in parts}
    for rule in PAIR_RULES:
        left, right = rule["groups"]
        if left in groups and right in groups:
            errors.append({"code": rule["id"], "message": rule["message"]})
    if "sliding" in groups and template["id"] in SLIDING_BLOCKED:
        errors.append(
            {
                "code": "sliding_template",
                "message": f"Раздвижная фурнитура не подходит к шаблону «{template['name']}».",
            }
        )
    return errors


def _thickness_errors(payload: dict[str, Any], parts: list[dict[str, Any]]) -> list[dict[str, str]]:
    raw = payload.get("glass_thick_mm")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return []
    thick = int(raw)
    errors: list[dict[str, str]] = []
    for part in parts:
        rule = _matching_thickness_rule(part)
        if rule is None:
            continue
        allowed = tuple(int(value) for value in rule["thickness_mm"])
        if thick in allowed:
            continue
        name = _clip(part.get("name") or part.get("sku") or "Позиция", 120)
        errors.append(
            {
                "code": rule["id"],
                "message": f"«{name}» {rule['text']} и не ставится на стекло {thick} мм.",
            }
        )
    return errors


def _angle_hardware_errors(
    template: dict[str, Any], payload: dict[str, Any], parts: list[dict[str, Any]]
) -> list[dict[str, str]]:
    if not template.get("angle_ne_90"):
        return []
    angle = payload.get("angle_deg")
    angle_value = int(angle) if isinstance(angle, (int, float)) and not isinstance(angle, bool) else None
    errors: list[dict[str, str]] = []
    for part in parts:
        if str(part.get("group_id") or "") != "hinges":
            continue
        blob = _blob(part)
        name = _clip(part.get("name") or part.get("sku") or "Петля", 120)
        if _is_right_angle_hinge(blob):
            shown = f"{angle_value}°" if angle_value is not None else "не 90°"
            errors.append(
                {
                    "code": "hinge_right_angle",
                    "message": (
                        f"«{name}» рассчитана на прямой угол и не ставится на шаблон "
                        f"«{template['name']}» (угол {shown})."
                    ),
                }
            )
        if "135" in blob and angle_value is not None and abs(angle_value - 135) > 15:
            errors.append(
                {
                    "code": "hinge_135",
                    "message": (
                        f"«{name}» рассчитана на 135° и не подходит к углу {angle_value}° "
                        "(допуск ±15°)."
                    ),
                }
            )
    return errors


def _hinge_count_findings(
    template: dict[str, Any],
    payload: dict[str, Any],
    parts: list[dict[str, Any]],
    *,
    as_error: bool,
) -> list[dict[str, str]]:
    """Too few hinges is an error once any hinge is chosen; none at all is a warning."""
    if _has_group(parts, "sliding") and not _has_group(parts, "hinges"):
        return []
    height = payload.get("height_mm")
    width = payload.get("width_mm")
    if not _is_int(height) or not _is_int(width):
        return []
    doors = max(1, _door_count(template, payload))
    leaf = max(1, int(width) // doors) if doors > 1 else int(width)
    need = min_hinges_per_door(int(height), leaf) * doors
    qty = _group_qty(parts, "hinges")
    if as_error:
        if qty <= 0 or qty >= need:
            return []
        return [
            {
                "code": "hinges_count",
                "message": (
                    f"Для высоты {int(height)} мм и створки {leaf} мм нужно не меньше {need} петель, "
                    f"выбрано {qty}."
                ),
            }
        ]
    if qty > 0:
        return []
    return [
        {
            "code": "hinges_count",
            "message": (
                f"Для высоты {int(height)} мм и створки {leaf} мм обычно ставят не меньше {need} петель. "
                "В спецификации петель нет."
            ),
        }
    ]


def _clamp_warnings(
    template: dict[str, Any], payload: dict[str, Any], parts: list[dict[str, Any]]
) -> list[dict[str, str]]:
    panels = sum(1 for slot in template["slots"] if slot["kind"] == "panel")
    if panels < 1 or _has_group(parts, "sliding"):
        return []
    height = payload.get("height_mm")
    if not _is_int(height):
        return []
    per_panel = min_clamps_per_panel(int(height))
    need = per_panel * panels
    qty = _group_qty(parts, "connectors")
    if qty >= need:
        return []
    return [
        {
            "code": "clamps_count",
            "message": (
                f"Для глухих панелей ({panels} шт., высота {int(height)} мм) обычно нужно "
                f"не меньше {need} коннекторов или зажимов, сейчас {qty}."
            ),
        }
    ]


def min_hinges_per_door(height_mm: int, leaf_mm: int) -> int:
    for step in HINGE_STEPS:
        if height_mm <= step["max_height"] and leaf_mm <= step["max_leaf"]:
            return step["min_hinges"]
    return 3


def min_clamps_per_panel(height_mm: int) -> int:
    for step in CLAMP_STEPS:
        if height_mm <= step["max_height"]:
            return step["min_clamps"]
    return 3


def _door_count(template: dict[str, Any], payload: dict[str, Any]) -> int:
    choices = tuple(int(value) for value in (template.get("door_choices") or ()))
    raw = payload.get("doors")
    if raw is None:
        raw = payload.get("door_count")
    if choices:
        if _is_int(raw):
            return int(raw)
        return choices[0]
    total = 0
    for slot in template["slots"]:
        if slot["kind"] == "door":
            total += int(slot.get("count") or 1)
    return total or 1


def _matching_thickness_rule(part: dict[str, Any]) -> Optional[dict[str, Any]]:
    for rule in THICKNESS_RULES:
        if _rule_matches(rule, part):
            return rule
    return None


def _rule_matches(rule: dict[str, Any], part: dict[str, Any]) -> bool:
    pinned = tuple(int(value) for value in (rule.get("part_ids") or ()))
    part_id = part.get("id")
    if pinned and _is_int(part_id) and int(part_id) in pinned:
        return True
    if pinned and not rule.get("group_id") and not rule.get("any"):
        return False
    group_id = rule.get("group_id")
    if group_id and str(part.get("group_id") or "") != group_id:
        return False
    blob = _blob(part)
    needles = tuple(str(token).lower() for token in (rule.get("any") or ()))
    if needles and not any(token in blob for token in needles):
        return False
    blocked = tuple(str(token).lower() for token in (rule.get("none") or ()))
    if any(token in blob for token in blocked):
        return False
    if not needles and not pinned:
        return False
    return True


def _blob(part: dict[str, Any]) -> str:
    text = f"{part.get('sku') or ''} {part.get('name') or ''}".lower().replace("ё", "е")
    return text.replace("–", "-").replace("—", "-")


def _is_right_angle_hinge(blob: str) -> bool:
    return "90°" in blob or "90º" in blob or "90 гр" in blob


def _has_group(parts: list[dict[str, Any]], group_id: str) -> bool:
    return any(str(part.get("group_id") or "") == group_id for part in parts)


def _group_qty(parts: list[dict[str, Any]], group_id: str) -> int:
    return sum(int(part.get("qty") or 0) for part in parts if str(part.get("group_id") or "") == group_id)


def _is_int(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) == value


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
