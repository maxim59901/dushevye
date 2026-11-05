"""ServicePLS — Bitrix Aspro Max, .catalog-block-view__item."""
from __future__ import annotations

from typing import List

from parsers.base import Http, Item, abs_url, guess_glass, guess_sku, item_raw, parse_price
from parsers.classify import classify

SOURCE = "servicepls"
BASE = "https://servicepls.ru"
START = f"{BASE}/catalog/furnitura/"
NOTES = (
    "Bitrix Aspro Max. Артикул в .article_block, цена itemprop=price. "
    "Пагинация ?PAGEN_1=N (~20/стр). Без капчи."
)


def parse(http: Http, *, max_items: int = 50, max_pages: int = 2) -> List[Item]:
    items: List[Item] = []
    seen = set()
    for page in range(1, max_pages + 1):
        url = START if page == 1 else f"{START}?PAGEN_1={page}"
        soup = http.soft_soup(url)
        if soup is None:
            break
        cards = soup.select(".catalog-block-view__item, .js-notice-block.item_block")
        if not cards:
            break
        for card in cards:
            a = card.select_one(".item-title a, a.dark_link")
            if not a:
                continue
            name = a.get_text(" ", strip=True)
            href = abs_url(BASE, a.get("href") or "")
            if href in seen:
                continue
            seen.add(href)
            art = card.select_one(".article_block, .muted")
            sku = ""
            if art:
                sku = art.get_text(" ", strip=True).replace("Арт.:", "").replace("Арт:", "").strip()
            if not sku:
                sku = guess_sku(name, fallback=str(card.get("data-id") or ""))
            price_el = card.select_one("[itemprop=price], .price_value, .price")
            price = None
            if price_el and price_el.get("content"):
                try:
                    price = float(price_el["content"])
                except ValueError:
                    price = parse_price(price_el.get_text(" ", strip=True))
            else:
                price = parse_price(price_el.get_text(" ", strip=True) if price_el else "")
            stock = None
            st = card.get_text(" ", strip=True).lower()
            if "много" in st or "в наличии" in st:
                stock = True
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
                raw_json=item_raw(id=card.get("data-id"), page=page),
            )
            items.append(it)
            if len(items) >= max_items:
                return items
    return items
