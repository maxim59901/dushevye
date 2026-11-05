"""Группы комплектующих: петли, ручки, коннекторы, штанги, уплотнители, раздвижные, прочее."""
from __future__ import annotations

GROUPS = (
    ("hinges", "Петли"),
    ("handles", "Ручки"),
    ("connectors", "Коннекторы"),
    ("rods", "Штанги / трубы / профили"),
    ("seals", "Уплотнители"),
    ("sliding", "Раздвижные системы"),
    ("other", "Прочее"),
)

GROUP_LABELS = dict(GROUPS)


def classify(name: str, url: str = "") -> str:
    t = f"{name} {url}".lower()
    # транслит/латиница из URL поставщиков
    if any(
        k in t
        for k in (
            "раздвиж",
            "razdvizh",
            "слайд",
            "квадрослайд",
            "инфинити",
            "атлант",
            "atlant",
            "every",
            "каретк",
            "ролик",
            "rolik",
            "каретка",
        )
    ):
        return "sliding"
    if any(k in t for k in ("петл", "petl", "hinge", "стеклопетл", "steklopetl", "steklya")):
        return "hinges"
    if any(k in t for k in ("ручк", "ruchk", "кноб", "knob", "knobi", "полотенцедержат")):
        return "handles"
    if any(
        k in t
        for k in (
            "коннект",
            "konnektor",
            "соединител",
            "soedinitel",
            "стеклодержат",
            "stekloderzhatel",
            "креплен",
            "krepez",
            "зажим",
            "holder",
            "stabilizator",
            "стабилизатор",
        )
    ):
        return "connectors"
    if any(
        k in t
        for k in (
            "уплотнит",
            "uplotnitel",
            "магнитн",
            "magnit",
            "пвх",
            "pvh",
            "порог",
            "щёточ",
            "щеточ",
            "seal",
            "самокле",
        )
    ):
        return "seals"
    if any(
        k in t
        for k in (
            "штанг",
            "shtanga",
            "труб",
            "truba",
            "профиль",
            "profil",
            "направляющ",
            "trek",
            "трек",
            "рейк",
        )
    ):
        # магнитный профиль = уплотнитель (уже отловлен выше)
        return "rods"
    if any(k in t for k in ("комплект", "komplekt")) and any(
        k in t for k in ("8823", "8825", "8827", "8828", "8832", "душ", "перегород", "нишу", "ванн")
    ):
        return "sliding"
    if "комплект" in t and ("раздвиж" in t or "система" in t or "/catalog/index/131" in t or "every" in t):
        return "sliding"
    # Pauli раздвижные комплекты 88xx
    if any(k in t for k in ("комплект 88", "8823-", "8825-", "8827-", "8828-", "8832-")):
        return "sliding"
    return "other"
