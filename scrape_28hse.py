#!/usr/bin/env python3
"""Download filtered Hong Kong apartment rental listings from 28Hse."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


BASE_URL = "https://www.28hse.com/rent/apartment"
USER_AGENT = "hk-apartment-research/1.0 (+https://www.28hse.com/robots.txt)"
REQUEST_DELAY_SECONDS = 0.25
PAGE_SIZE = 15
DISTRICT_URLS = tuple(
    f"{BASE_URL}{path}"
    for path in (
        # Hong Kong Island
        "/a1/dg2",  # Central, Sheung Wan
        "/a1/dg1",  # Sai Ying Pun, Shek Tong Tsui
        "/a1/dg6",  # Tin Hau, Tai Hang
        # Kowloon
        "/a2/dg115",  # Whampoa
        "/a2/dg31",  # Hung Hom
        "/a2/dg30",  # Tsim Sha Tsui
        "/a2/dg120",  # Jordan
        "/a2/dg111",  # Yau Ma Tei
        "/a2/dg110",  # Mong Kok
        "/a2/dg29",  # Prince Edward
        "/a2/dg28",  # Tai Kok Tsui, Olympic, Kowloon Station
        "/a2/dg109",  # Lai King
        "/a2/dg107",  # Mei Foo
        "/a2/dg106",  # Cheung Sha Wan
        "/a2/dg27",  # Lai Chi Kok
        "/a2/dg26",  # Sham Shui Po, Shek Kip Mei, Nam Cheong
        "/a2/dg119",  # Yau Yat Tsuen
        "/a2/dg118",  # Ho Man Tin
        "/a2/dg25",  # Kowloon Tong
        "/a2/dg32",  # San Po Kong, Wong Tai Sin
        "/a2/dg108",  # Kai Tak
        "/a2/dg116",  # Kowloon City
        "/a2/dg24",  # To Kwa Wan
        "/a2/dg23",  # Diamond Hill, Lok Fu
        # New Territories
        "/a3/dg51",  # Tsing Yi
        "/a3/dg50",  # Kwai Chung, Kwai Fong
        "/a3/dg49",  # Tsuen Wan, Tai Wo Hau
        "/a3/dg40",  # Sai Kung, Clear Water Bay
    )
)

MIN_RENT_HKD = 7_000
MAX_RENT_HKD = 17_000
MIN_AREA_SQFT = 400
MAX_AREA_SQFT = 900
MIN_BEDROOMS = 1
MAX_BEDROOMS = 3
MAX_BUILDING_AGE_YEARS = 30

ALLOWED_FLOORS = frozenset({"高層", "中層"})
OPEN_KITCHEN = "開放式廚房"
ALLOWED_DISTRICTS = frozenset(
    {
        "中環",
        "上環",
        "西營盤",
        "石塘咀",
        "天后",
        "大坑",
        "黃埔",
        "紅磡",
        "尖沙咀",
        "佐敦",
        "油麻地",
        "旺角",
        "太子",
        "大角咀",
        "奧運",
        "九龍站",
        "荔景",
        "美孚",
        "長沙灣",
        "荔枝角",
        "深水埗",
        "石硤尾",
        "南昌",
        "又一村",
        "何文田",
        "九龍塘",
        "新蒲崗",
        "黃大仙",
        "啟德",
        "九龍城",
        "土瓜灣",
        "鑽石山",
        "樂富",
        "青衣",
        "葵涌",
        "葵芳",
        "荃灣",
        "大窩口",
        "西貢",
        "清水灣",
    }
)

# 28Hse exposes preset buckets. Detail-page filters are enforced locally so
# listings with incomplete server-side metadata are not excluded prematurely.
SEARCH_PARAMS = {
    "price": "2,3,4",  # HK$5,000-20,000
    "areaOption": "sales",  # usable/saleable area
    "areaRange": "2,3",  # 300-1,000 sqft
    "roomRange": "1,2,3",
}

CSV_FIELDS = [
    "listing_id",
    "title",
    "district",
    "property_type",
    "price_hkd",
    "usable_area_sqft",
    "bedrooms",
    "bathrooms",
    "floor",
    "kitchen_type",
    "building_age_years",
    "agency",
    "image_url",
    "url",
    "fetched_at",
    "enriched_at",
    "estate",
    "building_area_sqft",
    "address",
    "description",
    "latitude",
    "longitude",
    "rent_includes",
    "cooking_method",
    "primary_school_net",
    "secondary_school_net",
    "published_at",
    "updated_at",
    "expires_at",
    "image_urls",
]
CARD_FIELDS = [
    "listing_id",
    "title",
    "district",
    "property_type",
    "price_hkd",
    "usable_area_sqft",
    "bedrooms",
    "bathrooms",
    "agency",
    "image_url",
    "url",
    "fetched_at",
]
DETAIL_REQUIRED_FIELDS = frozenset(
    {"floor", "kitchen_type", "building_age_years"}
)
DEFAULT_CANDIDATES_PATH = Path("data/28hse_candidates.csv")
DEFAULT_ENRICHED_CACHE_PATH = Path("data/28hse_enriched.csv")


def build_url(page: int, base_url: str = BASE_URL) -> str:
    path = base_url if page == 1 else f"{base_url}/page-{page}"
    return f"{path}?{urlencode(SEARCH_PARAMS)}"


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Unable to download {url}: {exc}") from exc


def parse_item_list(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            return data
    raise RuntimeError("28Hse response did not contain an ItemList JSON-LD block")


def first_number(text: str) -> int | None:
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def first_match(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1).replace(",", "")) if match else None


def parse_json_ld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        json_type = data.get("@type")
        if json_type == "ItemPage" or (
            isinstance(json_type, list) and "ItemPage" in json_type
        ):
            return data
    return None


def parse_coordinate(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def parse_image_urls(entity: dict[str, Any]) -> str:
    images = entity.get("image", [])
    if isinstance(images, (str, dict)):
        images = [images]
    if not isinstance(images, list):
        return ""

    urls: list[str] = []
    for image in images:
        if isinstance(image, str):
            urls.append(image)
        elif isinstance(image, dict) and isinstance(image.get("url"), str):
            urls.append(image["url"])
    return json.dumps(urls, ensure_ascii=False)


def parse_listing(card: BeautifulSoup, fetched_at: str) -> dict[str, Any] | None:
    link = card.select_one('a.detail_page[href*="/property-"]')
    if link is None:
        return None

    url = link.get("href")
    if not isinstance(url, str):
        return None
    id_match = re.search(r"/property-(\d+)", url)
    if id_match is None:
        return None

    price_node = card.select_one("div.extra div.ui.right.floated.green.large.label")
    area_node = card.select_one("div.areaUnitPrice")
    room_node = card.select_one("div.extra div.tagLabels div.ui.label")
    if price_node is None or area_node is None or room_node is None:
        print(f"warning: incomplete listing card skipped: {url}", file=sys.stderr)
        return None

    price = first_match(price_node.get_text(" ", strip=True), r"\$([\d,]+)")
    area = first_number(area_node.get_text(" ", strip=True))
    room_text = room_node.get_text(" ", strip=True)
    bedrooms = first_match(room_text, r"(\d+)\s*房")
    bathrooms = first_match(room_text, r"(\d+)\s*浴室")
    if price is None or area is None or bedrooms is None:
        print(f"warning: unparseable listing card skipped: {url}", file=sys.stderr)
        return None

    title_link = card.select_one('div.header a.detail_page[href*="/property-"]') or link
    district_links = card.select("div.district_area a")
    image = card.select_one("img.detail_page_img")
    agency_node = card.select_one("div.companyName")
    property_type = (
        district_links[1].get_text(" ", strip=True)
        if len(district_links) > 1
        else ""
    )

    return {
        "listing_id": id_match.group(1),
        "title": title_link.get_text(" ", strip=True),
        "district": district_links[0].get_text(" ", strip=True)
        if district_links
        else "",
        "property_type": property_type,
        "price_hkd": price,
        "usable_area_sqft": area,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms or "",
        "agency": agency_node.get_text(" ", strip=True)
        if agency_node
        else "",
        "image_url": image.get("src", "") if image else "",
        "url": url,
        "fetched_at": fetched_at,
    }


def parse_listing_details(soup: BeautifulSoup) -> dict[str, Any]:
    details: dict[str, Any] = {}
    item_page = parse_json_ld(soup)
    entity = item_page.get("mainEntity", {}) if item_page else {}
    if isinstance(entity, dict):
        address = entity.get("address")
        if isinstance(address, dict):
            address = address.get("streetAddress")
        if isinstance(address, str):
            details["address"] = address

        description = entity.get("description")
        if isinstance(description, str):
            details["description"] = description

        geo = entity.get("geo")
        if isinstance(geo, dict):
            latitude = parse_coordinate(geo.get("latitude"))
            longitude = parse_coordinate(geo.get("longitude"))
            if latitude is not None:
                details["latitude"] = latitude
            if longitude is not None:
                details["longitude"] = longitude

        details["image_urls"] = parse_image_urls(
            entity if entity.get("image") else item_page or {}
        )

    if item_page:
        for source_key, output_key in (
            ("datePublished", "published_at"),
            ("dateModified", "updated_at"),
            ("expires", "expires_at"),
        ):
            value = item_page.get(source_key)
            if isinstance(value, str):
                details[output_key] = value

    for row in soup.select("tr"):
        label_node = row.select_one("td.table_left")
        value_node = row.select_one("td.table_right")
        if label_node is None or value_node is None:
            continue

        label = label_node.get_text(" ", strip=True)
        pair_value = value_node.select_one("div.pairValue")
        value = pair_value.get_text(" ", strip=True) if pair_value else ""

        if label == "單位樓層":
            details["floor"] = value
        elif label == "廚房類型":
            details["kitchen_type"] = value
        elif label == "建築面積":
            area = first_number(value)
            if area is not None:
                details["building_area_sqft"] = area
        elif label == "地區屋苑":
            if value:
                details["estate"] = value
            age = first_match(
                value_node.get_text(" ", strip=True),
                r"屋苑樓齡\s*:\s*(\d+)\s*年",
            )
            if age is not None:
                details["building_age_years"] = age
        elif label == "租金已包":
            details["rent_includes"] = value
        elif label == "廚房煮食模式":
            details["cooking_method"] = value
        elif label == "小學校網":
            details["primary_school_net"] = value
        elif label == "中學校網":
            details["secondary_school_net"] = value
        elif label == "物業地址" and value:
            details.setdefault("address", value)

    if "latitude" not in details or "longitude" not in details:
        for map_link in soup.select("a.googleMap[href]"):
            href = map_link.get("href", "")
            if not isinstance(href, str):
                continue
            match = re.search(
                r"initNearbyMap\(\s*(-?\d+(?:\.\d+)?)\s*,\s*"
                r"(-?\d+(?:\.\d+)?)",
                href,
            )
            if match:
                details.setdefault("latitude", float(match.group(1)))
                details.setdefault("longitude", float(match.group(2)))
                break

    return details


def district_is_allowed(district: str) -> bool:
    return any(
        part.strip() in ALLOWED_DISTRICTS
        for part in re.split(r"[,，、]", district)
        if part.strip()
    )


def matches_card_filters(listing: dict[str, Any]) -> bool:
    return (
        MIN_RENT_HKD <= int(listing["price_hkd"]) <= MAX_RENT_HKD
        and MIN_AREA_SQFT
        <= int(listing["usable_area_sqft"])
        <= MAX_AREA_SQFT
        and MIN_BEDROOMS <= int(listing["bedrooms"]) <= MAX_BEDROOMS
        and district_is_allowed(str(listing["district"]))
    )


def matches_filters(listing: dict[str, Any]) -> bool:
    building_age = listing.get("building_age_years")
    try:
        building_age = int(building_age)
    except (TypeError, ValueError):
        return False
    return (
        matches_card_filters(listing)
        and listing.get("floor") in ALLOWED_FLOORS
        and listing.get("kitchen_type") == OPEN_KITCHEN
        and building_age < MAX_BUILDING_AGE_YEARS
    )


class IncrementalCsvWriter:
    def __init__(self, path: Path, fieldnames: list[str]) -> None:
        self.path = path
        self.fieldnames = fieldnames
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.existing_ids: set[str] = set()
        has_content = self.path.exists() and self.path.stat().st_size > 0
        if has_content:
            with self.path.open("r", encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                if reader.fieldnames != fieldnames:
                    raise RuntimeError(
                        f"{path} has an incompatible CSV header; use a new path"
                    )
                self.existing_ids = {
                    row["listing_id"] for row in reader if row.get("listing_id")
                }

        self.output = self.path.open("a", encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.output, fieldnames=fieldnames)
        if not has_content:
            self.writer.writeheader()
            self.output.flush()
            self._sync()

    def _sync(self) -> None:
        self.output.flush()
        try:
            os.fsync(self.output.fileno())
        except OSError as exc:
            raise RuntimeError(f"Unable to persist {self.path}: {exc}") from exc

    def append(self, row: dict[str, Any]) -> bool:
        listing_id = str(row["listing_id"])
        if listing_id in self.existing_ids:
            return False
        self.writer.writerow({field: row.get(field, "") for field in self.fieldnames})
        self._sync()
        self.existing_ids.add(listing_id)
        return True

    def close(self) -> None:
        self.output.close()

    def __enter__(self) -> "IncrementalCsvWriter":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"CSV input does not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def scrape(
    max_pages: int | None,
    candidates_path: Path,
    page_limit: int | None = None,
) -> int:
    fetched_at = datetime.now(timezone.utc).isoformat()
    new_candidates = 0
    pages_scraped = 0

    with IncrementalCsvWriter(candidates_path, CARD_FIELDS) as output:
        for district_url in DISTRICT_URLS:
            if page_limit is not None and pages_scraped >= page_limit:
                break
            page = 1
            total_items: int | None = None
            while (
                (max_pages is None or page <= max_pages)
                and (page_limit is None or pages_scraped < page_limit)
            ):
                url = build_url(page, district_url)
                print(f"downloading page {page}: {url}", file=sys.stderr)
                soup = BeautifulSoup(fetch_html(url), "html.parser")
                pages_scraped += 1
                cards = soup.select("div.listItems div.property_item")
                try:
                    item_list = parse_item_list(soup)
                except RuntimeError:
                    if cards:
                        raise
                    print(
                        f"no listings in district scope: {district_url}",
                        file=sys.stderr,
                    )
                    break

                if total_items is None:
                    total_items = int(item_list["numberOfItems"])
                    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
                    print(
                        f"candidate listings: {total_items}; pages: {total_pages}",
                        file=sys.stderr,
                    )

                for card in cards:
                    listing = parse_listing(card, fetched_at)
                    if listing is not None and matches_card_filters(listing):
                        if output.append(listing):
                            new_candidates += 1

                if not cards or (total_items is not None and page * PAGE_SIZE >= total_items):
                    break
                page += 1
                time.sleep(REQUEST_DELAY_SECONDS)

    return new_candidates


def enrich(
    candidates_path: Path,
    output_path: Path,
    cache_path: Path,
) -> int:
    if output_path.resolve() == cache_path.resolve():
        raise RuntimeError("--output and --cache must be different paths")
    candidates = read_csv_rows(candidates_path)
    new_enriched = 0
    with IncrementalCsvWriter(cache_path, CSV_FIELDS) as cache:
        for candidate in candidates:
            listing_id = candidate.get("listing_id", "")
            if not listing_id or listing_id in cache.existing_ids:
                continue

            url = candidate.get("url", "")
            if not url:
                print(
                    f"warning: candidate {listing_id} has no detail URL",
                    file=sys.stderr,
                )
                continue

            print(f"enriching listing {listing_id}: {url}", file=sys.stderr)
            detail_soup = BeautifulSoup(fetch_html(url), "html.parser")
            details = parse_listing_details(detail_soup)
            if not details:
                print(
                    f"warning: no listing details found: {url}",
                    file=sys.stderr,
                )
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            listing: dict[str, Any] = dict(candidate)
            listing.update(details)
            listing["enriched_at"] = datetime.now(timezone.utc).isoformat()
            if cache.append(listing):
                new_enriched += 1
            time.sleep(REQUEST_DELAY_SECONDS)

    enriched_rows = read_csv_rows(cache_path)
    matches = [listing for listing in enriched_rows if matches_filters(listing)]
    matches.sort(
        key=lambda item: (
            int(item["price_hkd"]),
            item["district"],
            item["listing_id"],
        )
    )
    write_csv(output_path, matches)
    return new_enriched


def write_csv(path: Path, listings: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(listings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        nargs="?",
        choices=("all", "scrape", "enrich"),
        default="all",
        help="run scraping, enrichment, or both (default: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/28hse_rentals.csv"),
        help="CSV output path (default: data/28hse_rentals.csv)",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="incremental candidate CSV (default: data/28hse_candidates.csv)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_ENRICHED_CACHE_PATH,
        help="incremental detail cache (default: data/28hse_enriched.csv)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="limit pages per district for a test run; default downloads all pages",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="limit total listing pages across all districts",
    )
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.stage in ("all", "scrape"):
        new_candidates = scrape(args.max_pages, args.candidates, args.limit)
        print(
            f"saved {new_candidates} new card-filtered listings to {args.candidates}"
        )

    if args.stage in ("all", "enrich"):
        new_enriched = enrich(args.candidates, args.output, args.cache)
        print(
            f"enriched {new_enriched} new listings; "
            f"wrote matching listings to {args.output}"
        )


if __name__ == "__main__":
    main()
