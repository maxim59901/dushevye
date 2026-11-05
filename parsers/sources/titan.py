"""ТИТАН — Bitrix/Aspro Premier, HTML-карточки .catalog-block__item."""
from __future__ import annotations

from typing import List
from urllib.parse import urljoin

from parsers.base import Http, Item, abs_url, guess_glass, guess_sku, item_raw, parse_price
from parsers.classify import classify

SOURCE = "titan"
BASE = "https://furnitura-titan.ru"
START = f"{BASE}/catalog/furnitura_dlya_dushevykh_kabin/"
NOTES = (
    "Bitrix Aspro Premier. Цены и названия в HTML. Пагинация ?PAGEN_1=N. "
    "Антибот не замечен при умеренном rate limit."
)


def parse(http: Http, *, max_items: int = 50, max_pages: int = 2) -> List[Item]:
    items: List[Item] = []
    seen = set()
    for page in range(1, max_pages + 1):
        url = START if page == 1 else f"{START}?PAGEN_1={page}"
        soup = http.soft_soup(url)
        if soup is None:
            break
        cards = soup.select(".catalog-block__item")
        if not cards:
            break
        for card in cards:
            a = card.select_one(".catalog-block__info-title a, .js-popup-title, a.dark_link")
            if not a:
                continue
            name = a.get_text(" ", strip=True)
            href = abs_url(BASE, a.get("href") or "")
            if href in seen:
                continue
            seen.add(href)
            price_el = card.select_one(".price__new-val, .price")
            price = parse_price(price_el.get_text(" ", strip=True) if price_el else "")
            stock = None
            st = card.get_text(" ", strip=True).lower()
            if "в наличии" in st:
                stock = True
            elif "под заказ" in st or "нет в наличии" in st:
                stock = False
            sku = guess_sku(name, fallback=card.get("id", "").split("_")[-1])
            it = Item(
                source=SOURCE,
                sku=sku,
                name=name,
                price=price,
                currency="RUB",
                url=href,
                in_stock=stock,
                glass_mm=guess_glass(name),
                group=classify(name, href),
                raw_json=item_raw(id=card.get("id"), page=page),
            )
            items.append(it)
            if len(items) >= max_items:
                return items
    return items
