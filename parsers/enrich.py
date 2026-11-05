"""Допарсинг карточек: фото, материал, отделка, характеристики."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from parsers.base import Http, Item, abs_url, guess_glass

FINISH_KEYS = (
    "цвет/отделка",
    "цвет",
    "отделка",
    "покрытие",
    "исполнение",
    "финиш",
    "finish",
    "color",
)
MATERIAL_KEYS = (
    "материал изготовления",
    "материал",
    "material",
)
GLASS_KEYS = (
    "толщина стекла",
    "стекло",
    "glass",
)

SKIP_IMG_RE = re.compile(
    r"(logo|icon|banner|pixel|counter|favicon|sprite|flag_|yandex|facebook|mail\.ru|"
    r"1x1|spacer|blank|placeholder|photo450x450)",
    re.I,
)
FINISH_FROM_NAME = re.compile(
    r"\b(хром(?:\s+мат(?:овый)?)?|черный(?:\s+мат)?|чёрный(?:\s+мат)?|"
    r"золото|бронза|никель|сатин|графит|белый|black|chrome|gold|"
    r"gm|sc|pc|cp|gp|sblc|ss)\b",
    re.I,
)


def _norm_key(k: str) -> str:
    return re.sub(r"\s+", " ", (k or "").strip().lower().replace("ё", "е"))


def _clean_val(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "").strip())


def extract_props(soup: BeautifulSoup) -> Dict[str, str]:
    props: Dict[str, str] = {}

    def put(k: str, v: str) -> None:
        k, v = _norm_key(k), _clean_val(v)
        if not k or not v or k in props:
            return
        if len(k) > 80 or len(v) > 200:
            return
        props[k] = v

    for item in soup.select(
        ".properties__item, .product-item-detail-properties-item, "
        ".props_item, .char_item, .product-props__item"
    ):
        name_el = item.select_one(
            ".properties__title, .properties__name, .char_name, "
            ".product-item-detail-properties-name, .props_item_title, dt, .name"
        )
        val_el = item.select_one(
            ".properties__value, .char_value, "
            ".product-item-detail-properties-value, .props_item_value, dd, .value"
        )
        if name_el and val_el:
            put(name_el.get_text(" ", strip=True), val_el.get_text(" ", strip=True))
            continue
        txt = item.get_text(" ", strip=True)
        if ":" in txt:
            a, b = txt.split(":", 1)
            put(a, b)
        elif "—" in txt or "–" in txt:
            parts = re.split(r"[—–]", txt, maxsplit=1)
            if len(parts) == 2:
                put(parts[0], parts[1])

    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            put(cells[0].get_text(" ", strip=True), cells[1].get_text(" ", strip=True))

    for dl in soup.select("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            put(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))

    return props


def _pick_prop(props: Dict[str, str], keys: Tuple[str, ...]) -> Optional[str]:
    for k in keys:
        if k in props:
            return props[k]
    for pk, pv in props.items():
        for k in keys:
            if k in pk:
                return pv
    return None


def extract_image(soup: BeautifulSoup, page_url: str, source: str) -> Optional[str]:
    og = soup.select_one('meta[property="og:image"], meta[property="og:image:secure_url"]')
    if og and og.get("content"):
        u = abs_url(page_url, og["content"].strip())
        if u.startswith("http") and not SKIP_IMG_RE.search(u):
            return u

    candidates: List[Tuple[int, str]] = []

    def consider(raw: Optional[str], score: int) -> None:
        if not raw or raw.startswith("data:"):
            return
        u = abs_url(page_url, raw.strip())
        if not u.startswith("http") or SKIP_IMG_RE.search(u):
            return
        # prefer non-resized originals
        sc = score
        if "resize_cache" in u:
            sc -= 20
        if "/upload/iblock/" in u or "/photo/products/" in u:
            sc += 30
        if "/images/catalog/" in u:
            sc += 25
        if "thumb" in u:
            sc -= 5
        if u.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            sc += 5
        candidates.append((sc, u))

    for sel in (
        'img[itemprop="image"]',
        ".item-bigimage",
        ".detail-gallery img",
        ".product-detail-gallery img",
        ".catalog-detail__gallery img",
        ".product__gallery img",
        ".gallery img",
        ".swiper-slide img",
        "a[data-fancybox] img",
    ):
        for el in soup.select(sel):
            for attr in ("data-src", "data-lazyload-src", "data-big", "data-original", "src"):
                if el.get(attr):
                    consider(el.get(attr), 50)
                    break
            parent = el.parent
            if parent and parent.name == "a" and parent.get("href"):
                href = parent.get("href")
                if href and any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                    consider(href, 55)

    for a in soup.select("a[data-fancybox], .detail-gallery-big a, a[href*='/upload/']"):
        href = a.get("href") or ""
        if any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            consider(href, 45)

    # source-specific
    if source == "pauli":
        m = re.search(r"/catalog/show/(\d+)", page_url)
        pid = m.group(1) if m else ""
        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src") or ""
            if pid and f"/images/catalog/{pid}/" in src:
                consider(src, 80)
            elif "/images/catalog/" in src and "thumb" in src:
                consider(src, 40)
    if source == "syndicate":
        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src") or ""
            if "/photo/products/" in src:
                consider(src, 80)
    if source == "arabella":
        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src") or ""
            if "/upload/iblock/" in src:
                consider(src, 70)

    if not candidates:
        for img in soup.select("img"):
            for attr in ("data-src", "src"):
                if img.get(attr):
                    consider(img.get(attr), 10)
                    break

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def finish_from_name(name: str) -> Optional[str]:
    m = FINISH_FROM_NAME.search(name or "")
    if not m:
        return None
    raw = m.group(1)
    mapping = {
        "gm": "Графит мат",
        "sc": "Сатин хром",
        "pc": "Полированный хром",
        "cp": "Хром",
        "gp": "Золото",
        "sblc": "Чёрный",
        "ss": "Нержавейка",
    }
    key = raw.lower()
    if key in mapping:
        return mapping[key]
    return raw[0].upper() + raw[1:] if raw else None


def enrich_item(http: Http, item: Item) -> Item:
    if not item.url:
        return item
    try:
        soup = http.soup(item.url)
    except Exception as e:
        # keep previous fields; note error in params
        err = {"_enrich_error": str(e)[:200]}
        if item.params_json:
            try:
                prev = json.loads(item.params_json)
                if isinstance(prev, dict):
                    prev.update(err)
                    item.params_json = json.dumps(prev, ensure_ascii=False)
                    return item
            except Exception:
                pass
        item.params_json = json.dumps(err, ensure_ascii=False)
        return item

    props = extract_props(soup)
    image = extract_image(soup, item.url, item.source)
    if image:
        item.image_url = image

    material = _pick_prop(props, MATERIAL_KEYS)
    finish = _pick_prop(props, FINISH_KEYS)
    glass = _pick_prop(props, GLASS_KEYS)

    if material:
        item.material = material
    if finish:
        item.finish = finish
    elif not item.finish:
        item.finish = finish_from_name(item.name)

    if glass:
        # normalize "8, 10" / "8-10 мм"
        g = guess_glass(glass + " мм") or glass
        item.glass_mm = g
    elif not item.glass_mm:
        item.glass_mm = guess_glass(item.name)

    # drop mapped keys from params dump to avoid duplication noise
    drop = set()
    for k in list(props):
        if any(x in k for x in MATERIAL_KEYS + FINISH_KEYS + GLASS_KEYS):
            drop.add(k)
    params = {k: v for k, v in props.items() if k not in drop}
    item.params_json = json.dumps(params, ensure_ascii=False) if params else ""
    return item


def enrich_all(http: Http, items: List[Item], *, progress_every: int = 20) -> List[Item]:
    out: List[Item] = []
    n = len(items)
    for i, it in enumerate(items, 1):
        out.append(enrich_item(http, it))
        if i % progress_every == 0 or i == n:
            with_img = sum(1 for x in out if x.image_url)
            print(f"  enrich {i}/{n} (с фото: {with_img})", flush=True)
    return out


def local_image_name(item: Item, image_url: str) -> str:
    ext = Path(urlparse(image_url).path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    if ext == ".jpeg":
        ext = ".jpg"
    key = f"{item.source}|{item.sku}|{item.url}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    safe_sku = re.sub(r"[^\w\-]+", "_", item.sku or "sku")[:40]
    return f"{item.source}_{safe_sku}_{digest}{ext}"


def download_images(
    http: Http,
    items: List[Item],
    media_dir: Path,
    *,
    public_prefix: str = "/media/parts/",
) -> int:
    media_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for it in items:
        if not it.image_url or it.image_url.startswith(public_prefix):
            if it.image_url and it.image_url.startswith(public_prefix):
                ok += 1
            continue
        remote = it.image_url
        name = local_image_name(it, remote)
        dest = media_dir / name
        try:
            if not dest.exists():
                # no delay stacking: use session get with tiny delay already in Http
                r = http.get(remote, stream=True)
                with dest.open("wb") as f:
                    for chunk in r.iter_content(65536):
                        if chunk:
                            f.write(chunk)
            it.image_url = public_prefix + name
            ok += 1
        except Exception as e:
            print(f"  !! image fail {it.sku}: {e}", flush=True)
            # keep remote URL as fallback
    return ok
