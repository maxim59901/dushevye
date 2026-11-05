"""Catalog compatibility on top of the template rules.

Seed and custom rows live in app.db (catalog_compat_rules). They point at a
catalog group, a name fragment, or a part id. Nothing here writes catalog.db.

A deny wins over an allow. Parts the code thickness dictionary already rejects
keep that error; this layer adds the rest (group × thickness, door vs fixed
panel, and rules an admin saved without a code change).
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .config_rules import get_template, matching_thickness_denial
from .db import get_db

GROUP_LABELS: tuple[tuple[str, str], ...] = (
    ("hinges", "Петли"),
    ("handles", "Ручки"),
    ("connectors", "Коннекторы"),
    ("rods", "Штанги / трубы / профили"),
    ("seals", "Уплотнители"),
    ("sliding", "Раздвижные системы"),
    ("other", "Прочее"),
)
GROUP_IDS = frozenset(item[0] for item in GROUP_LABELS)
GROUP_TITLE = dict(GROUP_LABELS)
TEMPLATE_KINDS = frozenset({"any", "door", "fixed"})
EFFECTS = frozenset({"allow", "deny"})
_META_KEY = "catalog_compat_seeded"


class CompatError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _seed(
    *,
    group_id: str,
    reason: str,
    effect: str = "deny",
    name_like: str = "",
    glass_mm: Optional[int] = None,
    template_id: str = "",
    template_kind: str = "any",
) -> dict[str, Any]:
    return {
        "effect": effect,
        "group_id": group_id,
        "group_name": GROUP_TITLE.get(group_id, ""),
        "name_like": name_like,
        "glass_mm": glass_mm,
        "template_id": template_id,
        "template_kind": template_kind,
        "reason": reason,
    }


# Matrix group × thickness, plus which groups fit a door or a fixed panel.
# 8-10 in a fragment also covers 8/10 after normalisation.
SEED_RULES: tuple[dict[str, Any], ...] = (
    _seed(group_id="hinges", name_like="8-10", glass_mm=6, reason="Серия 8–10 мм не ставится на стекло 6 мм."),
    _seed(group_id="hinges", name_like="4-6", glass_mm=8, reason="Серия 4–6 мм не ставится на стекло 8 мм."),
    _seed(group_id="hinges", name_like="4-6", glass_mm=10, reason="Серия 4–6 мм не ставится на стекло 10 мм."),
    _seed(group_id="hinges", name_like="только 10", glass_mm=6, reason="Петля только на 10 мм не ставится на стекло 6 мм."),
    _seed(group_id="hinges", name_like="только 10", glass_mm=8, reason="Петля только на 10 мм не ставится на стекло 8 мм."),
    _seed(group_id="hinges", name_like="только 8", glass_mm=6, reason="Петля только на 8 мм не ставится на стекло 6 мм."),
    _seed(group_id="hinges", name_like="только 8", glass_mm=10, reason="Петля только на 8 мм не ставится на стекло 10 мм."),
    _seed(group_id="connectors", name_like="8-10", glass_mm=6, reason="Зажим или коннектор 8–10 мм не ставится на стекло 6 мм."),
    _seed(group_id="connectors", name_like="4-6", glass_mm=8, reason="Зажим или коннектор 4–6 мм не ставится на стекло 8 мм."),
    _seed(group_id="connectors", name_like="4-6", glass_mm=10, reason="Зажим или коннектор 4–6 мм не ставится на стекло 10 мм."),
    _seed(group_id="connectors", name_like="зажим 8", glass_mm=6, reason="Зажим на 8 мм не ставится на стекло 6 мм."),
    _seed(group_id="connectors", name_like="зажим 6", glass_mm=8, reason="Зажим на 6 мм не ставится на стекло 8 мм."),
    _seed(group_id="connectors", name_like="зажим 6", glass_mm=10, reason="Зажим на 6 мм не ставится на стекло 10 мм."),
    _seed(group_id="seals", name_like="уплотнитель 8", glass_mm=6, reason="Уплотнитель на 8 мм не ставится на стекло 6 мм."),
    _seed(group_id="seals", name_like="уплотнитель 8", glass_mm=10, reason="Уплотнитель на 8 мм не ставится на стекло 10 мм."),
    _seed(group_id="seals", name_like="уплотнитель 10", glass_mm=6, reason="Уплотнитель на 10 мм не ставится на стекло 6 мм."),
    _seed(group_id="seals", name_like="уплотнитель 10", glass_mm=8, reason="Уплотнитель на 10 мм не ставится на стекло 8 мм."),
    _seed(group_id="seals", name_like="seal 8", glass_mm=6, reason="Уплотнитель seal 8 не ставится на стекло 6 мм."),
    _seed(group_id="seals", name_like="seal 8", glass_mm=10, reason="Уплотнитель seal 8 не ставится на стекло 10 мм."),
    _seed(group_id="seals", name_like="seal 10", glass_mm=6, reason="Уплотнитель seal 10 не ставится на стекло 6 мм."),
    _seed(group_id="seals", name_like="seal 10", glass_mm=8, reason="Уплотнитель seal 10 не ставится на стекло 8 мм."),
    _seed(group_id="rods", name_like="8-10", glass_mm=6, reason="Профиль 8–10 мм не ставится на стекло 6 мм."),
    _seed(group_id="rods", name_like="4-6", glass_mm=8, reason="Профиль 4–6 мм не ставится на стекло 8 мм."),
    _seed(group_id="rods", name_like="4-6", glass_mm=10, reason="Профиль 4–6 мм не ставится на стекло 10 мм."),
    _seed(group_id="hinges", template_kind="fixed", reason="Петли ставят на дверь, для глухой панели они не подходят."),
    _seed(group_id="handles", template_kind="fixed", reason="Ручки ставят на дверь, не на глухую панель."),
    _seed(
        group_id="sliding",
        template_id="corner_two",
        reason="Раздвижная система не ставится на угловую с распашной дверью.",
    ),
    _seed(
        group_id="sliding",
        template_id="ushape",
        reason="Раздвижная система не ставится на П-образный шаблон.",
    ),
    _seed(
        group_id="sliding",
        template_id="trapezoid",
        reason="Раздвижная система не ставится на трапецию.",
    ),
    _seed(group_id="hinges", name_like="8-10", glass_mm=8, effect="allow", reason="Серия 8–10 мм подходит к стеклу 8 мм."),
    _seed(group_id="hinges", name_like="8-10", glass_mm=10, effect="allow", reason="Серия 8–10 мм подходит к стеклу 10 мм."),
    _seed(group_id="hinges", name_like="4-6", glass_mm=6, effect="allow", reason="Серия 4–6 мм подходит к стеклу 6 мм."),
    _seed(group_id="connectors", name_like="8-10", glass_mm=8, effect="allow", reason="Зажим 8–10 мм подходит к стеклу 8 мм."),
    _seed(group_id="connectors", name_like="8-10", glass_mm=10, effect="allow", reason="Зажим 8–10 мм подходит к стеклу 10 мм."),
    _seed(group_id="connectors", name_like="4-6", glass_mm=6, effect="allow", reason="Зажим 4–6 мм подходит к стеклу 6 мм."),
    _seed(group_id="seals", name_like="уплотнитель 8", glass_mm=8, effect="allow", reason="Уплотнитель на 8 мм подходит к стеклу 8 мм."),
    _seed(group_id="seals", name_like="уплотнитель 10", glass_mm=10, effect="allow", reason="Уплотнитель на 10 мм подходит к стеклу 10 мм."),
    _seed(group_id="handles", template_kind="door", effect="allow", reason="Ручки уместны на двери."),
    _seed(group_id="connectors", template_kind="fixed", effect="allow", reason="Зажимы и коннекторы уместны на глухой панели."),
)

if not 15 <= len(SEED_RULES) <= 40:
    raise RuntimeError(f"catalog compat seed must stay between 15 and 40 rules, got {len(SEED_RULES)}")


def catalog_path() -> Path:
    return Path(os.environ.get("DUSHEVYE_CATALOG_DB", "/var/www/dushevye/data/catalog.db"))


def seed_catalog_compat(conn: sqlite3.Connection) -> None:
    """Insert the matrix once. Deleting a seed row does not bring it back."""
    row = conn.execute("SELECT value FROM app_meta WHERE key=?", (_META_KEY,)).fetchone()
    if row:
        return
    conn.executemany(
        """INSERT INTO catalog_compat_rules (
               org_id, origin, effect, group_id, group_name, part_id, name_like,
               glass_mm, template_id, template_kind, reason
           ) VALUES (NULL, 'seed', ?, ?, ?, NULL, ?, ?, ?, ?, ?)""",
        [
            (
                item["effect"],
                item["group_id"],
                item["group_name"],
                item["name_like"],
                item["glass_mm"],
                item["template_id"],
                item["template_kind"],
                item["reason"],
            )
            for item in SEED_RULES
        ],
    )
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, '1')", (_META_KEY,))


def load_compat_rules(org_id: Optional[int]) -> list[dict[str, Any]]:
    with get_db() as conn:
        if org_id is None:
            rows = conn.execute(
                """SELECT * FROM catalog_compat_rules
                   WHERE org_id IS NULL
                   ORDER BY id"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM catalog_compat_rules
                   WHERE org_id IS NULL OR org_id=?
                   ORDER BY id""",
                (int(org_id),),
            ).fetchall()
    return [_rule_dict(row) for row in rows]


def list_rules(org_id: int) -> list[dict[str, Any]]:
    return [_public_rule(item) for item in load_compat_rules(org_id)]


def create_rule(org_id: int, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    fields = _clean_payload(payload)
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO catalog_compat_rules (
                   org_id, origin, effect, group_id, group_name, part_id, name_like,
                   glass_mm, template_id, template_kind, reason, created_by
               ) VALUES (?, 'custom', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(org_id),
                fields["effect"],
                fields["group_id"],
                fields["group_name"],
                fields["part_id"],
                fields["name_like"],
                fields["glass_mm"],
                fields["template_id"],
                fields["template_kind"],
                fields["reason"],
                int(user_id),
            ),
        )
        row = conn.execute(
            "SELECT * FROM catalog_compat_rules WHERE id=?", (cur.lastrowid,)
        ).fetchone()
    return _public_rule(_rule_dict(row))


def delete_rule(org_id: int, rule_id: int) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM catalog_compat_rules WHERE id=?", (int(rule_id),)
        ).fetchone()
        if row is None:
            raise CompatError("Правило не найдено.", 404)
        owner = row["org_id"]
        if owner is not None and int(owner) != int(org_id):
            raise CompatError("Правило не найдено.", 404)
        conn.execute("DELETE FROM catalog_compat_rules WHERE id=?", (int(rule_id),))
    return {"ok": True, "id": int(rule_id)}


def opening_kind(template_id: str) -> str:
    """door — есть створка (в том числе вместе с глухой панелью); fixed — только глухая."""
    template = get_template((template_id or "").strip())
    if template is None:
        return "any"
    slots = template.get("slots") or ()
    if any(slot.get("kind") == "door" for slot in slots):
        return "door"
    if any(slot.get("kind") == "panel" for slot in slots):
        return "fixed"
    return "any"


def make_context(
    glass_mm: Optional[int],
    template_id: str = "",
    opening: Optional[str] = None,
) -> dict[str, Any]:
    template_id = (template_id or "").strip()
    if opening is None:
        opening = opening_kind(template_id)
    return {
        "glass_mm": None if glass_mm is None else int(glass_mm),
        "template_id": template_id,
        "opening": opening,
    }


def evaluate_compat(
    glass_mm: Optional[int],
    template_id: str,
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Allow/deny hints for the picker. Reads catalog rows, never writes them."""
    ctx = make_context(glass_mm, template_id)
    groups, parts, catalog_ok = _read_catalog()
    if not groups:
        groups = [
            {"id": group_id, "title_ru": title, "sort": index}
            for index, (group_id, title) in enumerate(GROUP_LABELS)
        ]
    denied_groups = _group_denies(groups, rules, ctx)
    group_rows = []
    for group in groups:
        reason = denied_groups.get(group["id"], "")
        group_rows.append(
            {
                "group_id": group["id"],
                "group_name": group.get("title_ru") or GROUP_TITLE.get(group["id"], group["id"]),
                "effect": "deny" if reason else "allow",
                "reason": reason,
            }
        )
    part_rows = []
    for part in parts:
        message = denial_message(part, rules, ctx)
        if not message:
            continue
        part_rows.append(
            {
                "part_id": int(part["id"]),
                "group_id": part.get("group_id") or "",
                "effect": "deny",
                "reason": message,
            }
        )
    return {
        "glass_mm": ctx["glass_mm"],
        "template_id": ctx["template_id"] or None,
        "opening": ctx["opening"],
        "catalog": catalog_ok,
        "rule_count": len(rules),
        "groups": group_rows,
        "parts": part_rows,
    }


def denial_message(part: dict[str, Any], rules: list[dict[str, Any]], ctx: dict[str, Any]) -> Optional[str]:
    """One sentence for the picker. Thickness text matches validate when both fire."""
    glass = ctx.get("glass_mm")
    if isinstance(glass, int):
        found = matching_thickness_denial(part, glass)
        if found is not None:
            return found[1]
    reason = deny_reason(part, rules, ctx)
    if not reason:
        return None
    name = _clip(part.get("name") or part.get("sku") or "Позиция", 120)
    return f"«{name}»: {reason}"


def deny_reason(part: dict[str, Any], rules: list[dict[str, Any]], ctx: dict[str, Any]) -> Optional[str]:
    for rule in rules:
        if rule.get("effect") != "deny":
            continue
        if context_matches(rule, ctx) and part_matches(rule, part):
            return str(rule.get("reason") or "Позиция не подходит к этой конфигурации.")
    return None


def apply_compat(
    result: dict[str, Any],
    payload: dict[str, Any],
    parts: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add catalog-rule errors. Skip a part the thickness dictionary already rejected."""
    if not parts or not rules:
        return result
    extra = compat_errors(payload, parts, rules)
    if not extra:
        return result
    merged = dict(result)
    merged["errors"] = list(result.get("errors") or []) + extra
    merged["ok"] = False
    return merged


def compat_errors(
    payload: dict[str, Any],
    parts: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[dict[str, str]]:
    glass = _as_int(payload.get("glass_thick_mm"))
    ctx = make_context(glass, str(payload.get("template_id") or ""))
    errors: list[dict[str, str]] = []
    for part in parts:
        if glass is not None and matching_thickness_denial(part, glass) is not None:
            continue
        reason = deny_reason(part, rules, ctx)
        if not reason:
            continue
        name = _clip(part.get("name") or part.get("sku") or "Позиция", 120)
        errors.append({"code": "catalog_compat", "message": f"«{name}»: {reason}"})
    return errors


def context_matches(rule: dict[str, Any], ctx: dict[str, Any]) -> bool:
    glass = rule.get("glass_mm")
    if glass is not None and ctx.get("glass_mm") != int(glass):
        return False
    template_id = (rule.get("template_id") or "").strip()
    if template_id and template_id != (ctx.get("template_id") or ""):
        return False
    kind = rule.get("template_kind") or "any"
    if kind != "any" and kind != ctx.get("opening"):
        return False
    return True


def part_matches(rule: dict[str, Any], part: dict[str, Any]) -> bool:
    group_id = (rule.get("group_id") or "").strip()
    if group_id and group_id != str(part.get("group_id") or ""):
        return False
    group_name = (rule.get("group_name") or "").strip()
    if not group_id and group_name:
        title = _norm(part.get("group_title") or "")
        gid = _norm(part.get("group_id") or "")
        needle = _norm(group_name)
        if needle != title and needle != gid:
            return False
    part_id = rule.get("part_id")
    if part_id is not None and int(part_id) != int(part.get("id") or 0):
        return False
    name_like = (rule.get("name_like") or "").strip()
    if name_like and not fragment_in(_part_blob(part), name_like):
        return False
    if not group_id and not group_name and part_id is None and not name_like:
        return False
    return True


def fragment_in(blob: str, needle: str) -> bool:
    """Substring match. A following digit or hyphen does not count (8 ≠ 8-10)."""
    blob = _norm(blob)
    needle = _norm(needle)
    if not needle:
        return False
    start = 0
    while True:
        index = blob.find(needle, start)
        if index < 0:
            return False
        prev = blob[index - 1:index]
        nxt = blob[index + len(needle):index + len(needle) + 1]
        if prev and prev.isdigit():
            start = index + 1
            continue
        if nxt and (nxt.isdigit() or nxt == "-"):
            start = index + 1
            continue
        return True


def _group_denies(
    groups: list[dict[str, Any]], rules: list[dict[str, Any]], ctx: dict[str, Any]
) -> dict[str, str]:
    found: dict[str, str] = {}
    for group in groups:
        group_id = group["id"]
        title = _norm(group.get("title_ru") or "")
        for rule in rules:
            if rule.get("effect") != "deny" or not _is_group_wide(rule):
                continue
            if not context_matches(rule, ctx):
                continue
            rule_group = (rule.get("group_id") or "").strip()
            if rule_group:
                if rule_group != group_id:
                    continue
            else:
                needle = _norm(rule.get("group_name") or "")
                if needle != title and needle != _norm(group_id):
                    continue
            found[group_id] = str(rule.get("reason") or "")
            break
    return found


def _is_group_wide(rule: dict[str, Any]) -> bool:
    if (rule.get("name_like") or "").strip():
        return False
    if rule.get("part_id") is not None:
        return False
    return bool((rule.get("group_id") or "").strip() or (rule.get("group_name") or "").strip())


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    effect = str(payload.get("effect") or "").strip().lower()
    if effect not in EFFECTS:
        raise CompatError("Эффект правила: allow или deny.")
    group_id = str(payload.get("group_id") or "").strip()
    if group_id and group_id not in GROUP_IDS:
        raise CompatError("Неизвестная группа фурнитуры.")
    group_name = _clip(payload.get("group_name") or "", 80)
    if not group_name and group_id:
        group_name = GROUP_TITLE.get(group_id, "")
    name_like = _clip(payload.get("name_like") or "", 80)
    part_raw = payload.get("part_id")
    part_id: Optional[int]
    if part_raw is None or part_raw == "":
        part_id = None
    elif isinstance(part_raw, bool) or not isinstance(part_raw, int) or part_raw < 1:
        raise CompatError("part_id — номер позиции каталога.")
    else:
        part_id = int(part_raw)
    glass_raw = payload.get("glass_mm")
    if glass_raw is None or glass_raw == "":
        glass_mm = None
    elif isinstance(glass_raw, bool) or not isinstance(glass_raw, int) or glass_raw < 4 or glass_raw > 19:
        raise CompatError("Толщина стекла в правиле: целое число от 4 до 19 мм или пусто.")
    else:
        glass_mm = int(glass_raw)
    template_id = str(payload.get("template_id") or "").strip()
    if template_id and get_template(template_id) is None:
        raise CompatError("Неизвестный шаблон конструкции.")
    template_kind = str(payload.get("template_kind") or "any").strip().lower() or "any"
    if template_kind not in TEMPLATE_KINDS:
        raise CompatError("Тип шаблона в правиле: any, door или fixed.")
    reason = _clip(payload.get("reason") or "", 240)
    if not reason:
        raise CompatError("Напишите короткое объяснение, почему позиция подходит или нет.")
    if part_id is not None:
        part = _lookup_part(part_id)
        if part is not None:
            if group_id and part["group_id"] and group_id != part["group_id"]:
                raise CompatError("Позиция относится к другой группе каталога.")
            if not group_id:
                group_id = part["group_id"] or ""
                group_name = group_name or (part.get("group_title") or GROUP_TITLE.get(group_id, ""))
    if not group_id and not group_name and part_id is None and not name_like:
        raise CompatError("Укажите группу, фрагмент названия или part_id.")
    return {
        "effect": effect,
        "group_id": group_id,
        "group_name": group_name,
        "part_id": part_id,
        "name_like": name_like,
        "glass_mm": glass_mm,
        "template_id": template_id,
        "template_kind": template_kind,
        "reason": reason,
    }


def _lookup_part(part_id: int) -> Optional[dict[str, Any]]:
    path = catalog_path()
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT p.id, p.group_id, g.title_ru AS group_title
               FROM parts p
               LEFT JOIN part_groups g ON g.id = p.group_id
               WHERE p.id=?""",
            (int(part_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise CompatError("В каталоге нет такой позиции.")
    return {
        "id": int(row["id"]),
        "group_id": row["group_id"] or "",
        "group_title": row["group_title"] or "",
    }


def _read_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    path = catalog_path()
    if not path.is_file():
        return [], [], False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        groups = [
            {"id": row["id"], "title_ru": row["title_ru"], "sort": row["sort"]}
            for row in conn.execute(
                "SELECT id, title_ru, sort FROM part_groups ORDER BY sort, id"
            ).fetchall()
        ]
        parts = [
            {
                "id": int(row["id"]),
                "group_id": row["group_id"] or "",
                "sku": row["sku"] or "",
                "name": row["name"] or "",
                "glass_mm": row["glass_mm"] or "",
                "group_title": row["group_title"] or "",
            }
            for row in conn.execute(
                """SELECT p.id, p.group_id, p.sku, p.name, p.glass_mm, g.title_ru AS group_title
                   FROM parts p
                   LEFT JOIN part_groups g ON g.id = p.group_id
                   ORDER BY p.id"""
            ).fetchall()
        ]
    finally:
        conn.close()
    return groups, parts, True


def _rule_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "org_id": None if row["org_id"] is None else int(row["org_id"]),
        "origin": row["origin"],
        "effect": row["effect"],
        "group_id": row["group_id"] or "",
        "group_name": row["group_name"] or "",
        "part_id": None if row["part_id"] is None else int(row["part_id"]),
        "name_like": row["name_like"] or "",
        "glass_mm": None if row["glass_mm"] is None else int(row["glass_mm"]),
        "template_id": row["template_id"] or "",
        "template_kind": row["template_kind"] or "any",
        "reason": row["reason"] or "",
        "created_at": row["created_at"],
    }


def _public_rule(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "origin": item["origin"],
        "effect": item["effect"],
        "group_id": item["group_id"],
        "group_name": item["group_name"],
        "part_id": item["part_id"],
        "name_like": item["name_like"],
        "glass_mm": item["glass_mm"],
        "template_id": item["template_id"],
        "template_kind": item["template_kind"],
        "reason": item["reason"],
        "created_at": item["created_at"],
    }


def _part_blob(part: dict[str, Any]) -> str:
    return _norm(f"{part.get('sku') or ''} {part.get('name') or ''} {part.get('glass_mm') or ''}")


def _norm(value: Any) -> str:
    text = "" if value is None else str(value).lower().replace("ё", "е")
    text = text.replace("–", "-").replace("—", "-").replace("/", "-").replace("\\", "-")
    return re.sub(r"\s+", " ", text).strip()


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if int(value) != value:
        return None
    return int(value)


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
