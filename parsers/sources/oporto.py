"""Oporto — Bitrix Intec Universe, .catalog-section-element."""
from __future__ import annotations

from typing import List

from parsers.base import Http, Item, abs_url, guess_glass, guess_sku, item_raw, parse_price
from parsers.classify import classify

SOURCE = "oporto"
BASE = "https://oporto-shower.ru"
START = f"{BASE}/catalog/furnitura_dlya_dushevykh_ograzhdeniy/"
NOTES = (
    "Bitrix Intec. Цена текстом «N руб.» на карточке, имя в .element-name a. "
    "Пагинация ?PAGEN_1=N. Без капчи."
)


def parse(http: Http, *, max_items: int = 50, max_pages: int = 2) -> List[Item]:
    items: List[Item] = []
    seen = set()
    for page in range(1, max_pages + 1):
        url = START if page == 1 else f"{START}?PAGEN_1={page}"
        soup = http.soft_soup(url)
        if soup is None:
            break
        cards = soup.select(".catalog-section-element")
        if not cards:
            break
        for card in cards:
            a = card.select_one(".element-name a")
            if not a:
                continue
            name = a.get_text(" ", strip=True)
            href = abs_url(BASE, a.get("href") or "")
            if href in seen:
                continue
            seen.add(href)
            price = None
            for t in card.stripped_strings:
                if "руб" in t.lower() or "₽" in t:
                    price = parse_price(t)
                    if price is not None:
                        break
            sku = guess_sku(name, fallback=str(card.get("data-product-id") or ""))
            it = Item(
                source=SOURCE,
                sku=sku,
                name=name,
                price=price,
                currency="RUB",
                url=href,
                glass_mm=guess_glass(name),
                group=classify(name, href),
                raw_json=item_raw(id=card.get("data-product-id"), page=page),
            )
            items.append(it)
            if len(items) >= max_items:
                return items
    return items
