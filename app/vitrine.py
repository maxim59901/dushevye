"""Public storefront assistant: template hints, FAQ, and guest leads.

Rules answer without an API key. When OPENAI_API_KEY is set (and
DUSHEVYE_VITRINE_LLM is not off), a short completion may rewrite the
wording. The template id, sizes, and buttons stay on the rules. A rewrite
that names a price, a purchase figure, or a Meta messenger is dropped.

Exact prices are never calculated here. Glass rates and catalog purchase
prices stay in the configurator and the cabinet.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from .config_rules import get_template

VITRINE_LEAD_STATUS = "заявка с витрины"
DEMO_HREF = "/lk/login.html"
CONFIGURATOR_HREF = "/cfg.html"

_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("trapezoid", re.compile(r"трапец")),
    ("ushape", re.compile(r"(?:п|u)[\s\-–—]?образ")),
    ("niche_straight", re.compile(r"ниш")),
    (
        "corner_two",
        re.compile(r"углов(?:ая|ое|ой|ые|ую|ых)|l[\s\-–—]?образ"),
    ),
)
_STRAIGHT = re.compile(r"(?:^|\s)прям")
_PRICE_ASK = re.compile(
    r"сколько\s+стоит|стоимост|прайс|бюджет|не\s+дороже|поч[её]м|(?<![а-яё])цен[аыуеой]"
)
_FAQ_KP = re.compile(r"(?<![а-яёa-z])кп(?![а-яёa-z])|коммерческ")
_FAQ_TIME = re.compile(r"срок|как\s+долго|когда\s+будет|изготов")
_FAQ_DEMO = re.compile(r"демо|личн\w*\s+кабинет|(?<![а-яёa-z])лк(?![а-яёa-z])|как\s+войти|регистрац")
_NUMBER = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
_LABELED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("height_mm", re.compile(r"высот\w*\s*[:=]?\s*(\d{3,4})(?!\d)")),
    ("depth2_mm", re.compile(r"бок\s*[cс]\s*[:=]?\s*(\d{3,4})(?!\d)")),
    ("angle_deg", re.compile(r"угол\s*[:=]?\s*(\d{2,3})(?!\d)")),
    ("depth_mm", re.compile(r"(?:глубин\w*|бок\s*[bб]|бок)\s*[:=]?\s*(\d{3,4})(?!\d)")),
    ("width_mm", re.compile(r"(?:ширин\w*|фасад\w*)\s*[:=]?\s*(\d{3,4})(?!\d)")),
)
_UNSAFE_REPLY = re.compile(
    r"₽|руб|whats\s*app|wa\.me|instagram|facebook|https?://|www\.",
    re.IGNORECASE,
)
_DIM_ORDER = ("width_mm", "depth_mm", "height_mm", "depth2_mm", "angle_deg")

_PRICE_LINE = (
    "Точную цену и закуп здесь не считаю и не показываю — откройте конфигуратор."
)
_HELP = (
    "Напишите тип ограждения и размеры в миллиметрах: "
    "угловая, ниша, П-образная или трапеция. "
    "Например: «угловая 900×900». "
    "Точную цену без конфигуратора не называю — черновик КП собирается там."
)
_MANY = (
    "В сообщении несколько типов. Выберите один: угловая, ниша, П-образная или трапеция — "
    "и пришлите размеры в миллиметрах, например «угловая 900×900»."
)
_NEED_TYPE = (
    "Размеры вижу, а тип — нет. Напишите, какая конструкция: "
    "угловая, ниша, П-образная или трапеция."
)
_FAQ_TEXT = {
    "kp": (
        "КП — черновое коммерческое предложение: тип, размеры проёма, "
        "стекло по оценке площади и фурнитура из каталога. "
        "Его собирают в конфигураторе; после входа черновик сохраняется и открывается как PDF. "
        "Это не счёт: финальная сумма — после замера."
    ),
    "time": (
        "Срок изготовления на витрине не фиксирую: он зависит от замера, стекла и наличия фурнитуры. "
        "Черновик КП в конфигураторе собирается сразу. "
        "Оставьте заявку — срок назовут уже по карточке."
    ),
    "demo": (
        "Демо личного кабинета — это вход по учётке. "
        "Новую учётку создаёт админ или хозяин приглашением, саморегистрации на витрине нет. "
        "Страница входа: /lk/login.html."
    ),
}
_LLM_SYSTEM = (
    "Ты помощник публичной витрины «Станок» (душевые ограждения). "
    "Ответь по-русски, коротко, обычными предложениями. "
    "Не меняй шаблон и размеры из черновика. "
    "Не называй цену, сумму, ставку, скидку и закуп и не пиши слово «рубль». "
    "Не вставляй ссылки и адреса сайтов. "
    "Не предлагай WhatsApp, Instagram, Facebook и другие мессенджеры. "
    "Не обещай срок в днях. Если спрашивают стоимость — направь в конфигуратор без суммы."
)


class VitrineChatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = ""
    history: Optional[list[Any]] = None


class VitrineLeadIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""
    phone: str = ""
    comment: str = ""
    template_id: str = ""
    excerpt: str = ""


def answer_chat(message: str, history: Any = None) -> dict[str, Any]:
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Напишите вопрос.")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Сообщение длиннее 1000 символов.")
    prior = _prior_user_texts(history)
    payload = compose_reply(text, prior)
    source = "rules"
    reply = payload["reply"]
    if llm_enabled():
        polished = _try_llm(text, history, reply)
        if polished and reply_is_public_safe(polished):
            reply = polished
            source = "llm"
    payload["reply"] = reply
    payload["source"] = source
    return payload


def compose_reply(message: str, prior_user_texts: list[str]) -> dict[str, Any]:
    """Rule reply. `prior_user_texts` is older user turns, oldest first."""
    current = _parse_message(message)
    sequence = [*(prior_user_texts or []), message]
    template_id, conflict = _resolve_type(sequence)
    dims: dict[str, int] = {}
    if template_id:
        dims = _resolve_dims(template_id, sequence)
    paragraphs: list[str] = []
    template_public = None
    href = None
    show_configurator = bool(
        current["price"] or "kp" in current["faqs"] or "time" in current["faqs"]
    )

    if conflict:
        paragraphs.append(_MANY)
    elif template_id:
        template = get_template(template_id)
        href = _configurator_href(template_id, dims)
        template_public = {
            "id": template["id"],
            "name": template["name"],
            "summary": template["summary"],
            "href": href,
        }
        paragraphs.extend(
            _template_paragraphs(template, dims, _unused_numbers(template_id, sequence))
        )
        show_configurator = True
    elif _message_has_size(current):
        paragraphs.append(_NEED_TYPE)
        show_configurator = True
    elif not current["faqs"]:
        paragraphs.append(_HELP)
        show_configurator = True

    if current["price"] or template_id:
        paragraphs.append(_PRICE_LINE)
    for code in ("kp", "time", "demo"):
        if code in current["faqs"]:
            paragraphs.append(_FAQ_TEXT[code])

    if not paragraphs:
        paragraphs.append(_HELP)
        show_configurator = True

    # Drop a duplicated price sentence if HELP already covers a bare greeting
    # together with an explicit price line. Price line stays when we added it.
    reply = "\n\n".join(_unique(paragraphs))
    return {
        "reply": reply,
        "template": template_public,
        "dims": dims,
        "actions": _actions(href if show_configurator else None, include_configurator=show_configurator),
        "source": "rules",
    }


def reply_is_public_safe(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 1500:
        return False
    if "<" in cleaned or ">" in cleaned:
        return False
    if _UNSAFE_REPLY.search(cleaned):
        return False
    return True


def llm_enabled() -> bool:
    flag = os.environ.get("DUSHEVYE_VITRINE_LLM", "auto").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def create_vitrine_lead(conn, body: VitrineLeadIn) -> dict[str, Any]:
    name = _clean_name(body.name)
    phone = _clean_phone(body.phone)
    comment = _clean_comment(body.comment)
    template_id = _clean_template_id(body.template_id)
    excerpt = _clip(body.excerpt, 1500)
    org_id = _default_org_id(conn)
    cur = conn.execute(
        """INSERT INTO vitrine_leads (
             org_id, name, phone, comment, status, template_id, excerpt
           ) VALUES (?,?,?,?,?,?,?)""",
        (org_id, name, phone, comment, VITRINE_LEAD_STATUS, template_id, excerpt),
    )
    return {
        "id": int(cur.lastrowid),
        "org_id": org_id,
        "status": VITRINE_LEAD_STATUS,
        "name": name,
        "phone": phone,
    }


def _fold(text: str) -> str:
    return (text or "").casefold().replace("ё", "е")


def _parse_message(text: str) -> dict[str, Any]:
    folded = _fold(text)
    types: list[str] = []
    for template_id, pattern in _TYPE_PATTERNS:
        if pattern.search(folded) and template_id not in types:
            types.append(template_id)
    if not types and _STRAIGHT.search(folded):
        types.append("niche_straight")
    labeled, masked = _labeled_dims(folded)
    numbers = [int(match) for match in _NUMBER.findall(masked) if 300 <= int(match) <= 9999]
    faqs: list[str] = []
    if _FAQ_KP.search(folded):
        faqs.append("kp")
    if _FAQ_TIME.search(folded):
        faqs.append("time")
    if _FAQ_DEMO.search(folded):
        faqs.append("demo")
    return {
        "types": types,
        "labeled": labeled,
        "numbers": numbers,
        "price": bool(_PRICE_ASK.search(folded)),
        "faqs": faqs,
    }


def _labeled_dims(folded: str) -> tuple[dict[str, int], str]:
    found: dict[str, int] = {}
    masked = folded
    for key, pattern in _LABELED:
        match = pattern.search(masked)
        if not match:
            continue
        found[key] = int(match.group(1))
        masked = masked[: match.start()] + (" " * (match.end() - match.start())) + masked[match.end() :]
    return found, masked


def _message_has_size(parsed: dict[str, Any]) -> bool:
    return bool(parsed["labeled"] or parsed["numbers"])


def _resolve_type(sequence: list[str]) -> tuple[Optional[str], bool]:
    for text in reversed(sequence):
        found = _parse_message(text)["types"]
        if not found:
            continue
        if len(found) > 1:
            return None, True
        return found[0], False
    return None, False


def _resolve_dims(template_id: str, sequence: list[str]) -> dict[str, int]:
    dims: dict[str, int] = {}
    for text in reversed(sequence):
        parsed = _parse_message(text)
        found = _dims_for(template_id, parsed)
        for key, value in found.items():
            if key not in dims:
                dims[key] = value
    return dims


def _dims_for(template_id: str, parsed: dict[str, Any]) -> dict[str, int]:
    template = get_template(template_id)
    if not template:
        return {}
    dims, _unused = _assign_numbers(template_id, parsed["numbers"])
    for key, value in parsed["labeled"].items():
        dims[key] = value
    allowed = {dim["id"] for dim in template["dimensions"]}
    return {key: value for key, value in dims.items() if key in allowed}


def _assign_numbers(template_id: str, numbers: list[int]) -> tuple[dict[str, int], list[int]]:
    # A tall opening is a height. A number above the band stays a plan size,
    # so 5000 mm is reported as out of range instead of being called a height.
    low, high = (1500, 2600) if template_id == "niche_straight" else (1700, 2600)
    plan: list[int] = []
    height = None
    for number in numbers:
        if low <= number <= high and height is None:
            height = number
        else:
            plan.append(number)
    dims: dict[str, int] = {}
    if height is not None:
        dims["height_mm"] = height
    if template_id == "niche_straight":
        if plan:
            dims["width_mm"] = plan[0]
        return dims, plan[1:]
    if template_id == "ushape":
        if len(plan) >= 1:
            dims["width_mm"] = plan[0]
        if len(plan) >= 2:
            dims["depth_mm"] = plan[1]
        if len(plan) >= 3:
            dims["depth2_mm"] = plan[2]
        return dims, plan[3:]
    if plan:
        dims["width_mm"] = plan[0]
    if len(plan) >= 2:
        dims["depth_mm"] = plan[1]
    return dims, plan[2:]


def _unused_numbers(template_id: str, sequence: list[str]) -> list[int]:
    """Positional numbers from the newest sized message that did not fit a field."""
    for text in reversed(sequence):
        parsed = _parse_message(text)
        if not parsed["numbers"]:
            continue
        _dims, extra = _assign_numbers(template_id, parsed["numbers"])
        return extra
    return []


def _template_paragraphs(
    template: dict[str, Any], dims: dict[str, int], unused: list[int]
) -> list[str]:
    known, missing = _describe_dims(template, dims)
    lines = [f"Шаблон «{template['name']}»: {template['summary']}"]
    if known:
        lines.append("По сообщению: " + "; ".join(known) + ".")
    if unused:
        listed = ", ".join(str(number) for number in unused)
        lines.append(
            f"Числа {listed} к полям этого шаблона не отнёс — проверьте их в конфигураторе."
        )
    if missing:
        lines.append("В конфигураторе ещё нужно указать: " + ", ".join(missing) + ".")
    glass = ", ".join(str(item) for item in template["glass_thickness_mm"])
    lines.append(f"Стекло для шаблона — {glass} мм.")
    min_width = template.get("two_door_min_width")
    width = dims.get("width_mm")
    if min_width and width is not None and width >= int(min_width):
        lines.append(f"При ширине от {min_width} мм в этом шаблоне можно две створки.")
    return lines


def _describe_dims(template: dict[str, Any], dims: dict[str, int]) -> tuple[list[str], list[str]]:
    known: list[str] = []
    missing: list[str] = []
    for dim in template["dimensions"]:
        label = dim["label"]
        unit = dim.get("unit") or "мм"
        value = dims.get(dim["id"])
        if value is None:
            missing.append(f"{label} ({dim['min']}–{dim['max']} {unit})")
            continue
        if dim.get("not_equal") is not None and int(value) == int(dim["not_equal"]):
            known.append(f"{label} — {value} {unit}, для этого шаблона не {dim['not_equal']} {unit}")
            continue
        if int(value) < int(dim["min"]) or int(value) > int(dim["max"]):
            known.append(f"{label} — {value} {unit}, вне допуска {dim['min']}–{dim['max']} {unit}")
        else:
            known.append(f"{label} — {value} {unit}")
    return known, missing


def _configurator_href(template_id: str, dims: dict[str, int]) -> str:
    pairs = [("template", template_id)]
    for key in _DIM_ORDER:
        if key in dims:
            pairs.append((key, str(int(dims[key]))))
    return CONFIGURATOR_HREF + "?" + urlencode(pairs)


def _actions(href: Optional[str], *, include_configurator: bool) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if include_configurator:
        items.append(
            {
                "id": "configurator",
                "label": "Открыть конфигуратор",
                "href": href or CONFIGURATOR_HREF,
            }
        )
    items.append({"id": "lead", "label": "Оставить заявку"})
    items.append({"id": "demo", "label": "Демо ЛК", "href": DEMO_HREF})
    return items


def _unique(paragraphs: list[str]) -> list[str]:
    seen: list[str] = []
    for item in paragraphs:
        if item and item not in seen:
            seen.append(item)
    return seen


def _prior_user_texts(history: Any) -> list[str]:
    if history is None:
        return []
    if not isinstance(history, list):
        raise HTTPException(status_code=400, detail="История должна быть списком.")
    if len(history) > 12:
        raise HTTPException(status_code=400, detail="История длиннее 12 сообщений.")
    users: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            users.append(content[:1000])
    return users[-8:]


def _try_llm(message: str, history: Any, draft: str) -> Optional[str]:
    try:
        return complete_openai(_llm_messages(message, history, draft))
    except Exception:
        return None


def _llm_messages(message: str, history: Any, draft: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": _LLM_SYSTEM}]
    if isinstance(history, list):
        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = item.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            content = content.strip()
            if content:
                messages.append({"role": role, "content": content[:1000]})
    messages.append(
        {
            "role": "user",
            "content": f"Черновик ответа:\n{draft}\n\nВопрос гостя:\n{message.strip()[:1000]}",
        }
    )
    return messages


def complete_openai(messages: list[dict[str, str]]) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no api key")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    if not (base.startswith("https://") or base.startswith("http://")):
        raise RuntimeError("bad openai base url")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": messages,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read(48_000)
    except urllib.error.URLError as exc:
        raise RuntimeError("openai request failed") from exc
    data = json.loads(raw.decode("utf-8", errors="replace"))
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("empty openai reply")
    return content.strip()


def _default_org_id(conn) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM organizations WHERE slug=? ORDER BY id LIMIT 1",
        ("default",),
    ).fetchone()
    if not row:
        row = conn.execute("SELECT id FROM organizations ORDER BY id LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def _clean_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if len(name) < 2 or len(name) > 80:
        raise HTTPException(status_code=400, detail="Укажите имя — от 2 до 80 символов.")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", name):
        raise HTTPException(status_code=400, detail="В имени нужна хотя бы одна буква.")
    if not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё .'\-«»]{0,79}", name):
        raise HTTPException(status_code=400, detail="Имя содержит недопустимые символы.")
    return name


def _clean_phone(raw: str) -> str:
    phone = " ".join((raw or "").split())
    if not re.fullmatch(r"\+?[0-9][0-9\s()\-]{8,22}", phone):
        raise HTTPException(status_code=400, detail="Укажите телефон: цифры, можно с + и скобками.")
    digits = re.sub(r"\D", "", phone)
    if not 10 <= len(digits) <= 15:
        raise HTTPException(status_code=400, detail="В телефоне должно быть от 10 до 15 цифр.")
    return phone


def _clean_comment(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Комментарий длиннее 2000 символов.")
    return text


def _clean_template_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if get_template(value) is None:
        raise HTTPException(status_code=400, detail="Неизвестный шаблон.")
    return value


def _clip(raw: str, limit: int) -> str:
    text = (raw or "").replace("\x00", "").strip()
    if len(text) > limit:
        return text[:limit]
    return text
