"""Общие утилиты HTTP и нормализации позиций."""
from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_DELAY = 1.0  # секунд между запросами — умеренно


@dataclass
class Item:
    source: str
    sku: str
    name: str
    price: Optional[float]
    currency: str
    url: str
    raw_json: str = ""
    group: str = "other"
    in_stock: Optional[bool] = None
    glass_mm: Optional[str] = None
    image_url: Optional[str] = None
    material: Optional[str] = None
    finish: Optional[str] = None
    params_json: str = ""

    def to_row(self) -> dict:
        return {
            "source": self.source,
            "sku": self.sku,
            "name": self.name,
            "price": "" if self.price is None else self.price,
            "currency": self.currency,
            "url": self.url,
            "group": self.group,
            "in_stock": "" if self.in_stock is None else int(bool(self.in_stock)),
            "glass_mm": self.glass_mm or "",
            "image_url": self.image_url or "",
            "material": self.material or "",
            "finish": self.finish or "",
            "params_json": self.params_json or "",
            "raw_json": self.raw_json,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Item":
        price_raw = row.get("price", "")
        price = None
        if price_raw not in (None, ""):
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                price = None
        stock_raw = row.get("in_stock", "")
        in_stock = None
        if stock_raw not in (None, ""):
            in_stock = str(stock_raw).strip() in ("1", "true", "True", "yes")
        return cls(
            source=(row.get("source") or "").strip(),
            sku=(row.get("sku") or "").strip(),
            name=(row.get("name") or "").strip(),
            price=price,
            currency=(row.get("currency") or "RUB").strip() or "RUB",
            url=(row.get("url") or "").strip(),
            group=(row.get("group") or "other").strip() or "other",
            in_stock=in_stock,
            glass_mm=(row.get("glass_mm") or "").strip() or None,
            image_url=(row.get("image_url") or "").strip() or None,
            material=(row.get("material") or "").strip() or None,
            finish=(row.get("finish") or "").strip() or None,
            params_json=(row.get("params_json") or "").strip(),
            raw_json=(row.get("raw_json") or "").strip(),
        )


class Http:
    def __init__(self, delay: float = DEFAULT_DELAY, timeout: float = 180.0):
        self.delay = delay
        # (connect, read) — медленные витрины (Синдикат) иначе рвут весь прогон
        if isinstance(timeout, (int, float)):
            self.timeout = (15.0, float(timeout))
        else:
            self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        retry = Retry(
            total=3,
            connect=3,
            read=0,
            backoff_factor=1.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._last = 0.0

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.session.get(url, timeout=self.timeout, stream=stream)
        self._last = time.time()
        r.raise_for_status()
        if not stream:
            r.encoding = r.apparent_encoding or r.encoding
        return r

    def soup(self, url: str) -> BeautifulSoup:
        r = self.get(url)
        return BeautifulSoup(r.text, "lxml")

    def soft_soup(self, url: str, timeout=None, retries: int = 2):
        """Like soup(), but returns None on HTTP 404/410 (pagination past end).
        Transient network/timeouts are retried; persistent failure raises.
        """
        to = self.timeout if timeout is None else timeout
        if isinstance(to, (int, float)):
            to = (15.0, float(to))
        last_err = None
        for attempt in range(max(1, retries)):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.session.get(url, timeout=to)
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
                self._last = time.time()
                continue
            self._last = time.time()
            if r.status_code in (404, 410):
                return None
            if r.status_code in (502, 503, 504):
                last_err = requests.HTTPError(f"{r.status_code} for {url}")
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
                continue
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                last_err = e
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
                continue
            r.encoding = r.apparent_encoding or r.encoding
            return BeautifulSoup(r.text, "lxml")
        raise last_err if last_err else RuntimeError(f"soft_soup failed: {url}")



def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.replace("\xa0", " ").replace("\u202f", " ")
    t = re.sub(r"[^\d,.\s]", " ", t)
    t = t.strip()
    m = re.search(r"(\d{1,3}(?:[\s]\d{3})+|\d+)([.,]\d{1,2})?", t)
    if not m:
        return None
    whole = m.group(1).replace(" ", "")
    frac = m.group(2) or ""
    if frac:
        frac = "." + frac[1:]
    try:
        return float(whole + frac)
    except ValueError:
        return None


SKU_RE = re.compile(
    r"\b("
    r"TA-[\w\-]+|T-[\w\-]+|"  # titan
    r"A-[\w\-]+|"  # arabella
    r"SND[\w\-]+|\d{2,4}-[\w\-]+|"  # syndicate-ish
    r"RK[\w\-./]+|B\d{3,5}\w*|"  # servicepls / oporto
    r"\d{4}"  # pauli model
    r")\b",
    re.I,
)


def guess_sku(name: str, fallback: str = "") -> str:
    if not name:
        return fallback
    m = SKU_RE.search(name)
    if m:
        return m.group(1).strip()
    parts = name.split()
    if parts and re.search(r"[A-Za-zА-Яа-я]", parts[0]) and re.search(r"\d", parts[0]):
        return parts[0]
    return fallback or parts[0] if parts else ""


GLASS_RE = re.compile(r"(\d{1,2})\s*[-–_]?\s*(\d{1,2})?\s*мм", re.I)


def guess_glass(name: str) -> Optional[str]:
    m = GLASS_RE.search(name or "")
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    return f"{a}-{b}" if b else a


def abs_url(base: str, href: str) -> str:
    return urljoin(base, href or "")


CSV_FIELDS = [
    "source",
    "sku",
    "name",
    "price",
    "currency",
    "url",
    "group",
    "in_stock",
    "glass_mm",
    "image_url",
    "material",
    "finish",
    "params_json",
    "raw_json",
]


def write_csv(path: Path, items: Iterable[Item], *, allow_empty: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(items)
    if not rows and not allow_empty and path.exists() and path.stat().st_size > 80:
        # Keep previous non-empty CSV (failed/timeout scrape must not wipe).
        print(f"    skip empty write, keep existing {path}")
        return 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for it in rows:
            w.writerow(it.to_row())
    return len(rows)


def read_csv(path: Path) -> list[Item]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [Item.from_row(r) for r in csv.DictReader(f)]


def item_raw(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)
