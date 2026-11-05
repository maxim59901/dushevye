"""Арабелла — Bitrix, карточки .goods__card."""
from __future__ import annotations

from typing import List

from parsers.base import Http, Item, abs_url, guess_glass, guess_sku, item_raw, parse_price
from parsers.classify import classify

SOURCE = "arabella"
BASE = "https://arabella-st.ru"
START = f"{BASE}/katalog/furnitura-dlya-dushevykh-kabin/"
NOTES = (
    "Bitrix. Артикул в .goods__card_subtitle, цена в .goods__card_price. "
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
        cards = soup.select(".goods__card[data-entity='item'], .goods__card")
        if not cards:
            break
        for card in cards:
            a = card.select_one(".goods__card_title a, a[href*='/katalog/']")
            btn = card.select_one("[data-name]")
            href = ""
            name = ""
            if a:
                name = a.get_text(" ", strip=True)
                href = abs_url(BASE, a.get("href") or "")
            if (not name) and btn is not None:
                name = (btn.get("data-name") or "").strip()
            if (not href) and a is None:
                # fallback: любая ссылка на карточку
                a2 = card.select_one("a[href*='/katalog/']")
                if a2:
                    href = abs_url(BASE, a2.get("href") or "")
                    if not name:
                        name = a2.get_text(" ", strip=True)
            if not href:
                continue
            if not name:
                sub = card.select_one(".goods__card_subtitle")
                name = sub.get_text(strip=True) if sub else ""
            if not name:
                continue
            if href in seen:
                continue
            seen.add(href)
            sub = card.select_one(".goods__card_subtitle")
            sku = (sub.get_text(strip=True) if sub else "") or guess_sku(name)
            price_el = card.select_one(".goods__card_price, [data-entity='price-block']")
            price = parse_price(price_el.get_text(" ", strip=True) if price_el else "")
            it = Item(
                source=SOURCE,
                sku=sku,
                name=name,
                price=price,
                currency="RUB",
                url=href,
                glass_mm=guess_glass(name),
                group=classify(name, href),
                raw_json=item_raw(id=card.get("id"), page=page),
            )
            items.append(it)
            if len(items) >= max_items:
                return items
    return items
