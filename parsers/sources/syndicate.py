"""Синдикат (tcmo-pro.ru) — карточки div.item со ссылкой на .html."""
from __future__ import annotations

from typing import List
import re
from urllib.parse import urljoin

from parsers.base import Http, Item, abs_url, guess_glass, guess_sku, item_raw, parse_price
from parsers.classify import classify

SOURCE = "syndicate"
BASE = "https://tcmo-pro.ru"
START = f"{BASE}/furnitura_ck/"
# подкатегории фурнитуры (полный охват; сайту тяжело — ходим по разделам)
EXTRA = [
    f"{BASE}/furnitura_ck/petli-dlya-dushevih-kabin/",
    f"{BASE}/furnitura_ck/knobiruchki-dly-duchevih-kabin/",
    f"{BASE}/furnitura_ck/konnektori-dlya-verhney-obvyazki/",
    f"{BASE}/furnitura_ck/1-konnektori-dlya-stekla-cherez-otverstie/",
    f"{BASE}/furnitura_ck/uplotnitelniy-profil-pvh/",
    f"{BASE}/furnitura_ck/ddushkabin/",
    f"{BASE}/furnitura_ck/trubi-i-shtangi-razdvizhnie/",
    f"{BASE}/furnitura_ck/reguliruemie-stabilizatsionnie-shtangi/",
    f"{BASE}/furnitura_ck/profil-p-obrazniy-aisi304/",
]
NOTES = (
    "Свой движок. Товары в div.item, цена «от N р», название часто в URL/.html. "
    "Много вариантов (цвет/материал) — берём стартовую цену. Без капчи при умеренном лимите."
)


def _name_from_item(it, href: str) -> str:
    text = " ".join(it.get_text(" ", strip=True).split())
    m = re.search(r"\b([A-Z]{2,}[\w\-]*\d[\w\-]*|\d{2}-\d{2}[\w\-]*)\b", text)
    code = m.group(1) if m else ""
    slug = href.rstrip("/").split("/")[-1].replace(".html", "").replace("-", " ")
    if code and code.lower() not in slug.lower():
        return f"{code} {slug}".strip()
    if code:
        rest = text
        idx = text.find(code)
        if idx >= 0:
            rest = text[idx + len(code) :].strip()
            rest = re.split(r"Выберите|Материал,|Цена", rest)[0].strip(" -")
        return f"{code} {rest}".strip()[:180] or code
    return slug[:180] or text[:120]


def _discover_cats(http: Http) -> List[str]:
    """Собрать подкатегории с главной furnitura_ck (если страница открылась)."""
    try:
        soup = http.soup(START)
    except Exception as e:
        print(f"    syndicate discover failed: {e}")
        return []
    found = []
    seen = set()
    for a in soup.select('a[href*="/furnitura_ck/"]'):
        href = abs_url(BASE, a.get("href") or "")
        if ".html" in href:
            continue
        path = href.rstrip("/")
        if path.endswith("furnitura_ck") or path + "/" == START:
            continue
        # только один уровень каталога (не товар)
        if href.count("/") < 4:
            continue
        if href not in seen:
            seen.add(href)
            if not href.endswith("/"):
                href = href + "/"
            found.append(href)
    return found


def parse(http: Http, *, max_items: int = 50, max_pages: int = 2) -> List[Item]:
    items: List[Item] = []
    seen = set()
    urls = list(EXTRA) + [START]
    # при полном прогоне — добрать категории с главной
    # discover с главной отключён: tcmo-pro.ru часто таймаутит на корне
    if False and (max_pages >= 10 or max_items >= 200):
        for u in _discover_cats(http):
            if u not in urls:
                urls.append(u)
    # max_pages ограничивает число разделов только в пробном режиме
    limit_urls = len(urls) if (max_pages >= 10 or max_items >= 200) else max(1, max_pages + 3)
    for url in urls[:limit_urls]:
        try:
            soup = http.soft_soup(url, timeout=120)
        except Exception as e:
            print(f"    syndicate skip {url}: {e}")
            continue
        if soup is None:
            print(f"    syndicate skip {url}: 404")
            continue
        for card in soup.select("div.item"):
            a = card.select_one('a[href*=".html"]')
            if not a:
                continue
            href = abs_url(BASE, a.get("href") or "")
            if href in seen:
                continue
            if "/furnitura_ck/" not in href and "furnitura" not in href:
                continue
            seen.add(href)
            text = " ".join(card.get_text(" ", strip=True).split())
            price = None
            m = re.search(r"от\s*([\d\s.,]+)\s*р", text, re.I)
            if m:
                price = parse_price(m.group(1))
            else:
                m2 = re.search(r"([\d\s.,]+)\s*руб", text, re.I)
                if m2:
                    price = parse_price(m2.group(1))
            name = _name_from_item(card, href)
            sku = guess_sku(name, fallback=href.rstrip("/").split("/")[-1].replace(".html", "")[:40])
            it = Item(
                source=SOURCE,
                sku=sku,
                name=name,
                price=price,
                currency="RUB",
                url=href,
                glass_mm=guess_glass(name + " " + text),
                group=classify(name, href),
                raw_json=item_raw(list_url=url),
            )
            items.append(it)
            if len(items) >= max_items:
                return items
    return items
