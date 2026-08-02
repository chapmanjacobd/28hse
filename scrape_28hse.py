#!/usr/bin/env python3
"""Download filtered Hong Kong apartment rental listings from 28Hse."""

from __future__ import annotations

import argparse
import csv
import json
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

MIN_RENT_HKD = 7_000
MAX_RENT_HKD = 17_000
MIN_AREA_SQFT = 400
MAX_AREA_SQFT = 900
MIN_BEDROOMS = 1

# 28Hse exposes preset buckets. Exact boundaries are enforced below.
SEARCH_PARAMS = {
    "price": "2,3,4",  # HK$5,000-20,000
    "areaOption": "sales",  # usable/saleable area
    "areaRange": "2,3",  # 300-1,000 sqft
    "roomRange": "1,2,3,4,5PLUS",
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
    "agency",
    "image_url",
    "url",
    "fetched_at",
]


def build_url(page: int) -> str:
    path = BASE_URL if page == 1 else f"{BASE_URL}/page-{page}"
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


def matches_filters(listing: dict[str, Any]) -> bool:
    return (
        MIN_RENT_HKD <= listing["price_hkd"] <= MAX_RENT_HKD
        and MIN_AREA_SQFT <= listing["usable_area_sqft"] <= MAX_AREA_SQFT
        and listing["bedrooms"] >= MIN_BEDROOMS
    )


def scrape(max_pages: int | None) -> list[dict[str, Any]]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    results: dict[str, dict[str, Any]] = {}
    page = 1
    total_items: int | None = None

    while max_pages is None or page <= max_pages:
        url = build_url(page)
        print(f"downloading page {page}: {url}", file=sys.stderr)
        soup = BeautifulSoup(fetch_html(url), "html.parser")
        item_list = parse_item_list(soup)
        if total_items is None:
            total_items = int(item_list["numberOfItems"])
            total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
            print(f"candidate listings: {total_items}; pages: {total_pages}", file=sys.stderr)

        cards = soup.select("div.listItems div.property_item")
        for card in cards:
            listing = parse_listing(card, fetched_at)
            if listing and matches_filters(listing):
                results[listing["listing_id"]] = listing

        if not cards or (total_items is not None and page * PAGE_SIZE >= total_items):
            break
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return list(results.values())


def write_csv(path: Path, listings: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(listings)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/28hse_rentals.csv"),
        help="CSV output path (default: data/28hse_rentals.csv)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="limit pages for a test run; default downloads all candidate pages",
    )
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")

    listings = scrape(args.max_pages)
    listings.sort(key=lambda item: (item["price_hkd"], item["district"], item["listing_id"]))
    write_csv(args.output, listings)
    print(f"wrote {len(listings)} matching listings to {args.output}")


if __name__ == "__main__":
    main()
