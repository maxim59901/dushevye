"""PAULI Russia — серии → /catalog/show/<id>. Цены на витрине часто пустые (под заказ / B2B)."""
from __future__ import annotations

from typing import List, Set, Tuple
import re

from parsers.base import Http, Item, abs_url, guess_glass, guess_sku, item_raw, parse_price
from parsers.classify import classify

SOURCE = "pauli"
BASE = "https://www.paulirus.ru"
# листовые серии и разделы фурнитуры (без рекламы/демо)
SERIES = [
    # Петли
    f"{BASE}/catalog/index/200",  # Fardello
    f"{BASE}/catalog/index/19",   # Farfalla
    f"{BASE}/catalog/index/143",  # Flamea
    f"{BASE}/catalog/index/144",  # Flamea+
    f"{BASE}/catalog/index/146",  # Flamea+ SN
    f"{BASE}/catalog/index/18",   # Flinter
    f"{BASE}/catalog/index/17",   # Fluture
    f"{BASE}/catalog/index/119",  # Nivello
    f"{BASE}/catalog/index/120",  # Nivello+
    f"{BASE}/catalog/index/21",   # Papillon
    f"{BASE}/catalog/index/194",  # Pavone
    f"{BASE}/catalog/index/20",   # Pillango
    f"{BASE}/catalog/index/15",   # Pontere
    f"{BASE}/catalog/index/197",  # Tura
    # Коннекторы
    f"{BASE}/catalog/index/32",
    f"{BASE}/catalog/index/23",
    f"{BASE}/catalog/index/148",
    f"{BASE}/catalog/index/147",
    f"{BASE}/catalog/index/121",
    f"{BASE}/catalog/index/195",
    f"{BASE}/catalog/index/25",
    f"{BASE}/catalog/index/198",
    # Раздвижные / комплектующие
    f"{BASE}/catalog/index/131",
    f"{BASE}/catalog/index/133",  # ролики
    f"{BASE}/catalog/index/132",
    f"{BASE}/catalog/index/142",
    f"{BASE}/catalog/index/141",
    f"{BASE}/catalog/index/140",
    f"{BASE}/catalog/index/139",
    f"{BASE}/catalog/index/193",
    # Ручки / штанги / уплотнители / прочее
    f"{BASE}/catalog/index/8",
    f"{BASE}/catalog/index/9",
    f"{BASE}/catalog/index/199",
    f"{BASE}/catalog/index/36",
    f"{BASE}/catalog/index/35",
    f"{BASE}/catalog/index/37",
    f"{BASE}/catalog/index/10",
    f"{BASE}/catalog/index/11",
    f"{BASE}/catalog/index/38",
    f"{BASE}/catalog/index/122",
    f"{BASE}/catalog/index/124",
    f"{BASE}/catalog/index/125",
    f"{BASE}/catalog/index/208",
    f"{BASE}/catalog/index/126",
    f"{BASE}/catalog/index/12",
    f"{BASE}/catalog/index/127",
    f"{BASE}/catalog/index/192",
    f"{BASE}/catalog/index/14",
]
SKIP_INDEX = {"128", "129", "130", "201"}  # реклама / демо
NOTES = (
    "HTML-каталог серий → карточки /catalog/show/<id>. Имя и артикул есть; "
    "колонка «Цена» на карточке часто пустая (нужен запрос/логин). EUR→RUB курс на сайте."
)


def _collect_shows(soup, seen: Set[str]) -> List[Tuple[str, str, str]]:
    out = []
    for a in soup.select('a[href*="/catalog/show/"]'):
        href = abs_url(BASE, a.get("href") or "")
        m = re.search(r"/catalog/show/(\d+)", href)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        label = a.get_text(" ", strip=True)
        out.append((pid, href, label))
    return out


def _nested_indexes(soup) -> List[str]:
    urls = []
    for a in soup.select('a[href*="/catalog/index/"]'):
        href = abs_url(BASE, a.get("href") or "")
        m = re.search(r"/catalog/index/(\d+)", href)
        if not m:
            continue
        if m.group(1) in SKIP_INDEX:
            continue
        urls.append(href.rstrip("/"))
    # unique preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse(http: Http, *, max_items: int = 50, max_pages: int = 2) -> List[Item]:
    items: List[Item] = []
    seen: Set[str] = set()
    show_ids: List[Tuple[str, str, str]] = []

    series = list(SERIES)
    if max_pages >= 10 or max_items >= 200:
        # полный прогон — все серии; пробный — первые max_pages+2
        pass
    else:
        series = series[: max(1, max_pages + 2)]

    queued = list(series)
    visited_idx: Set[str] = set()
    while queued and len(show_ids) < max_items:
        url = queued.pop(0)
        m = re.search(r"/catalog/index/(\d+)", url)
        key = m.group(1) if m else url
        if key in visited_idx:
            continue
        visited_idx.add(key)
        try:
            soup = http.soup(url)
        except Exception as e:
            print(f"    pauli skip {url}: {e}")
            continue
        found = _collect_shows(soup, seen)
        show_ids.extend(found)
        # если карточек мало — заглянуть во вложенные индексы (полный режим)
        if (max_pages >= 10 or max_items >= 200) and len(found) < 3:
            for nested in _nested_indexes(soup):
                nm = re.search(r"/catalog/index/(\d+)", nested)
                nk = nm.group(1) if nm else nested
                if nk not in visited_idx and nested not in queued:
                    queued.append(nested)
        if len(show_ids) >= max_items:
            break

    for pid, href, label in show_ids[:max_items]:
        name = label if label and label.lower() not in ("подробнее...", "подробнее", "") else ""
        price = None
        currency = "EUR"
        stock = None
        if not name or len(name) < 4:
            try:
                soup = http.soup(href)
            except Exception as e:
                items.append(
                    Item(
                        source=SOURCE,
                        sku=pid,
                        name=f"PAULI #{pid}",
                        price=None,
                        currency="EUR",
                        url=href,
                        group="other",
                        raw_json=item_raw(error=str(e)),
                    )
                )
                continue
            h1 = soup.select_one("h1")
            name = h1.get_text(" ", strip=True) if h1 else soup.title.get_text(strip=True) if soup.title else f"PAULI #{pid}"
            for tr in soup.select("table tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.select("td")]
                if len(cells) >= 3:
                    p = parse_price(cells[2])
                    if p is not None:
                        price = p
                        break
            body = soup.get_text(" ", strip=True).lower()
            if "есть" in body and "под заказ" in body:
                stock = None
            elif re.search(r"\bесть\b", body):
                stock = True

        sku = guess_sku(name, fallback=pid)
        it = Item(
            source=SOURCE,
            sku=sku,
            name=name or f"PAULI #{pid}",
            price=price,
            currency=currency,
            url=href,
            in_stock=stock,
            glass_mm=guess_glass(name),
            group=classify(name, href),
            raw_json=item_raw(id=pid, list_label=label),
        )
        items.append(it)
        if len(items) >= max_items:
            break
    return items
