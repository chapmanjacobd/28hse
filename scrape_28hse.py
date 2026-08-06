#!/usr/bin/env python3
"""Download filtered Hong Kong apartment rental listings from 28Hse."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)


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
MIN_AREA_SQFT = 350
MAX_AREA_SQFT = 900
MIN_BEDROOMS = 1
MAX_BEDROOMS = 3
MAX_BUILDING_AGE_YEARS = 30

ALLOWED_FLOORS = frozenset({"高層", "中層"})
OPEN_KITCHEN = "開放式廚房"
UNKNOWN_DETAIL_VALUES = frozenset(
    {"", "-", "--", "—", "－", "n/a", "na", "null", "none"}
)
CONTACT_TEXT_FIELDS = frozenset(
    {
        "title",
        "district",
        "property_type",
        "floor",
        "kitchen_type",
        "agency",
        "estate",
        "address",
        "description",
        "rent_includes",
        "subletting",
        "cooking_method",
        "primary_school_net",
        "secondary_school_net",
        "sharing_type",
        "sharing_terms",
    }
)
EMAIL_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_.+-])[A-Za-z0-9_.+-]+@"
    r"(?:[A-Za-z0-9-]+\.)+[a-z]{2,}(?![A-Za-z0-9.-])"
)
PHONE_DIGIT_CHARS = (
    "0123456789"
    "０１２３４５６７８９"
    "零〇○"
    "一壹壱幺么"
    "二貳贰兩两"
    "三參叁参弎"
    "四肆"
    "五伍"
    "六陸陆"
    "七柒"
    "八捌"
    "九玖"
)
PHONE_FIRST_DIGIT_CHARS = (
    "23456789"
    "２３４５６７８９"
    "二貳贰兩两"
    "三參叁参弎"
    "四肆"
    "五伍"
    "六陸陆"
    "七柒"
    "八捌"
    "九玖"
)
PHONE_PATTERN = re.compile(
    rf"(?<![{PHONE_DIGIT_CHARS}])"
    rf"(?:(?:\+|00)?852[\s().-]*)?"
    rf"[{PHONE_FIRST_DIGIT_CHARS}](?:[\s().-]*[{PHONE_DIGIT_CHARS}]){{7}}"
    rf"(?![{PHONE_DIGIT_CHARS}])"
)
SOCIAL_USERNAME_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<prefix>"
    r"(?:wechat|whatsapp|instagram|微信)\s*(?:[:：=]\s*[-–—]?\s*|[-–—]\s+)"
    r")(?P<username>[A-Za-z0-9][A-Za-z0-9_.-]*)(?![A-Za-z0-9_.-])"
)
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
EXCLUDED_PROPERTY_TYPES = frozenset({"村屋"})

# Shared/co-rental detection. Listings matching a SHARED_PATTERNS pattern are
# excluded; ambiguous wording is only flagged on the output CSV for review.
# Negatives like 不可分租 or 無需合租 are stripped first so they do not
# accidentally match the 分租 / 合租 markers.
NEGATIVE_SHARED_PHRASES = (
    "不可分租",
    "不能分租",
    "不設分租",
    "不允分租",
    "不接受分租",
    "禁止分租",
    "拒絕分租",
    "免分租",
    "不分租",
    "無需合租",
    "不用合租",
    "不需合租",
    "免合租",
    "不設合租",
)
# (pattern, label) pairs; patterns may be plain substrings or regexes.
SHARED_PATTERNS = (
    ("分租", "分租"),
    (r"(?<!適)合租", "合租"),
    ("夾租", "夾租"),
    ("租一間", "租一間"),
    (r"(?<!放)床位(?!置)", "床位"),
    ("單間", "單間"),
    ("室友", "室友"),
    ("限女", "限女"),
    ("限男", "限男"),
    ("共用", "共用"),
)
WHOLE_MARKERS = frozenset(
    {
        "全租",
        "整套出租",
        "整間出租",
        "整租",
        "獨立單位",
        "獨享",
    }
)
AMBIGUOUS_MARKERS = frozenset(
    {
        "唐樓分層出租",
        "分層出租",
        "套房",
        "兩房一廳",
        "二房一廳",
        "2房1廳",
        "兩房兩廳",
        "可住1人",
        "可住一人",
        "包水電",
        "水電全包",
        "水電費全包",
        "獨立水電錶",
        "獨立電錶",
        "獨立水錶",
        "宿舍",
    }
)
# 2-bedroom layouts (兩房一廳, 2房1廳, ...): usually a whole flat, but
# sometimes only one room of it is being rented.
ROOM_LAYOUT_PATTERN = re.compile(
    r"[2２兩二]\s*房\s*[0-9０-９一二兩三四五六七八九十]+\s*廳"
)

# 28Hse exposes preset buckets. Detail-page filters are enforced locally so
# listings with incomplete server-side metadata are not excluded prematurely.
SEARCH_PARAMS = {
    "price": "2,3,4",  # HK$5,000-20,000
    "areaOption": "sales",  # usable/saleable area
    "areaRange": "2,3",  # 300-1,000 sqft
    "roomRange": "1,2,3",
    "sortBy": "latest",
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
    "subletting",
    "cooking_method",
    "primary_school_net",
    "secondary_school_net",
    "published_at",
    "updated_at",
    "expires_at",
    "image_urls",
]
OUTPUT_FIELDS = [
    *CSV_FIELDS,
    "sharing_type",
    "sharing_terms",
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
DEFAULT_CANDIDATES_PATH = Path("data/28hse_candidates.csv")
DEFAULT_ENRICHED_CACHE_PATH = Path("data/28hse_enriched.csv")


def strip_contact_info(value: Any, field: str | None = None) -> Any:
    """Remove contact details and social-media usernames from CSV text fields."""
    if not isinstance(value, str):
        return value

    sanitized = EMAIL_PATTERN.sub("[REDACTED]", value)
    if field in CONTACT_TEXT_FIELDS:
        sanitized = PHONE_PATTERN.sub("[REDACTED]", sanitized)
        sanitized = SOCIAL_USERNAME_PATTERN.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]",
            sanitized,
        )
    return sanitized


def sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        field: strip_contact_info(value, field)
        for field, value in row.items()
    }


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


def listing_id_from_card(card: BeautifulSoup) -> str | None:
    link = card.select_one('a.detail_page[href*="/property-"]')
    if link is None:
        return None

    url = link.get("href")
    if not isinstance(url, str):
        return None
    id_match = re.search(r"/property-(\d+)", url)
    return id_match.group(1) if id_match else None


def parse_listing(card: BeautifulSoup, fetched_at: str) -> dict[str, Any] | None:
    link = card.select_one('a.detail_page[href*="/property-"]')
    listing_id = listing_id_from_card(card)
    if link is None or listing_id is None:
        return None

    url = link.get("href")
    if not isinstance(url, str):
        return None

    price_node = card.select_one("div.extra div.ui.right.floated.green.large.label")
    area_node = card.select_one("div.areaUnitPrice")
    room_node = card.select_one("div.extra div.tagLabels div.ui.label")
    if price_node is None or area_node is None or room_node is None:
        LOG.debug("incomplete listing card skipped: %s", url)
        return None

    price = first_match(price_node.get_text(" ", strip=True), r"\$([\d,]+)")
    area = first_number(area_node.get_text(" ", strip=True))
    room_text = room_node.get_text(" ", strip=True)
    bedrooms = first_match(room_text, r"(\d+)\s*房")
    bathrooms = first_match(room_text, r"(\d+)\s*浴室")
    if price is None or area is None or bedrooms is None:
        LOG.debug("unparseable listing card skipped: %s", url)
        return None

    title_link = card.select_one('div.header a.detail_page[href*="/property-"]') or link
    district_links = card.select("div.district_area a")
    image = card.select_one("img.detail_page_img")
    agency_node = card.select_one("div.companyName")
    property_type = (
        district_links[1].get_text(" ", strip=True) if len(district_links) > 1 else ""
    )

    return sanitize_row({
        "listing_id": listing_id,
        "title": title_link.get_text(" ", strip=True),
        "district": (
            district_links[0].get_text(" ", strip=True) if district_links else ""
        ),
        "property_type": property_type,
        "price_hkd": price,
        "usable_area_sqft": area,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms or "",
        "agency": agency_node.get_text(" ", strip=True) if agency_node else "",
        "image_url": image.get("src", "") if image else "",
        "url": url,
        "fetched_at": fetched_at,
    })


def parse_property_types(soup: BeautifulSoup) -> str:
    for labels in soup.select("div.ui.labels"):
        texts = [
            label.get_text(" ", strip=True)
            for label in labels.find_all("div", class_="label", recursive=False)
        ]
        if texts and texts[0] == "住宅":
            return "/".join(texts[1:])
    return ""


def parse_subletting(soup: BeautifulSoup) -> str:
    for row in soup.select("tr"):
        label_node = row.select_one("td.table_left")
        if label_node is None or label_node.get_text(" ", strip=True) != "每月租金":
            continue
        sub_value = row.select_one("td.table_right div.pairSubValue")
        return sub_value.get_text(" ", strip=True) if sub_value else ""
    return ""


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
        elif label == "實用面積":
            area = first_number(value)
            if area is not None:
                details["usable_area_sqft"] = area
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
                r"initNearbyMap\(\s*(-?\d+(?:\.\d+)?)\s*,\s*" r"(-?\d+(?:\.\d+)?)",
                href,
            )
            if match:
                details.setdefault("latitude", float(match.group(1)))
                details.setdefault("longitude", float(match.group(2)))
                break

    if details.get("latitude") == 0.0 and details.get("longitude") == 0.0:
        details.pop("latitude", None)
        details.pop("longitude", None)

    property_types = parse_property_types(soup)
    if property_types:
        details["property_type"] = property_types
    subletting = parse_subletting(soup)
    if subletting:
        details["subletting"] = subletting

    return sanitize_row(details)


def district_is_allowed(district: str) -> bool:
    return any(
        part.strip() in ALLOWED_DISTRICTS
        for part in re.split(r"[,，、]", district)
        if part.strip()
    )


def normalized_detail_value(value: Any) -> str:
    return " ".join(str(value or "").split())


def is_unknown_detail_value(value: Any) -> bool:
    return normalized_detail_value(value).casefold() in UNKNOWN_DETAIL_VALUES


def property_type_is_excluded(property_type: str) -> bool:
    return any(
        part in EXCLUDED_PROPERTY_TYPES
        for part in re.split(r"[/、,，]", property_type)
        if part
    )


def classify_sharing(listing: dict[str, Any]) -> tuple[str, list[str]]:
    """Classify a listing as whole-unit, shared, or ambiguous.

    Shared markers (分租, 合租, 夾租, 租一間, 床位, 單間, 室友, 限女/限男,
    共用) exclude the listing. Ambiguous wording (套房, 兩房一廳, 包水電,
    獨立水電錶, ...) is returned as "ambiguous" so the row is kept but flagged
    for manual review. Listings without any marker default to "whole".
    Returns (sharing_type, matched_terms).
    """
    text = " ".join(
        normalized_detail_value(listing.get(field))
        for field in ("title", "description", "subletting")
    )
    for phrase in NEGATIVE_SHARED_PHRASES:
        text = text.replace(phrase, "")

    shared_terms = [
        label for pattern, label in SHARED_PATTERNS if re.search(pattern, text)
    ]
    if shared_terms:
        return "shared", shared_terms

    whole_terms = [marker for marker in WHOLE_MARKERS if marker in text]
    if whole_terms:
        return "whole", whole_terms

    ambiguous_terms = [marker for marker in AMBIGUOUS_MARKERS if marker in text]
    for match in ROOM_LAYOUT_PATTERN.finditer(text):
        layout = match.group(0).replace(" ", "")
        if layout not in ambiguous_terms:
            ambiguous_terms.append(layout)
    if ambiguous_terms:
        return "ambiguous", ambiguous_terms

    return "whole", []


def matches_card_filters(listing: dict[str, Any]) -> bool:
    return (
        MIN_RENT_HKD <= int(listing["price_hkd"]) <= MAX_RENT_HKD
        and MIN_AREA_SQFT <= int(listing["usable_area_sqft"]) <= MAX_AREA_SQFT
        and MIN_BEDROOMS <= int(listing["bedrooms"]) <= MAX_BEDROOMS
        and district_is_allowed(str(listing["district"]))
        and not property_type_is_excluded(str(listing["property_type"]))
    )


def filter_rejections(listing: dict[str, Any]) -> list[str]:
    """Return human-readable reasons a listing fails the detail filters."""
    reasons: list[str] = []

    try:
        price = int(listing["price_hkd"])
    except (KeyError, TypeError, ValueError):
        reasons.append("price is unparseable")
    else:
        if not MIN_RENT_HKD <= price <= MAX_RENT_HKD:
            reasons.append(
                f"price HK${price:,} outside HK${MIN_RENT_HKD:,}-{MAX_RENT_HKD:,}"
            )

    try:
        area = int(listing["usable_area_sqft"])
    except (KeyError, TypeError, ValueError):
        reasons.append("usable area is unparseable")
    else:
        if not MIN_AREA_SQFT <= area <= MAX_AREA_SQFT:
            reasons.append(
                f"usable area {area} sqft outside {MIN_AREA_SQFT}-{MAX_AREA_SQFT}"
            )

    try:
        bedrooms = int(listing["bedrooms"])
    except (KeyError, TypeError, ValueError):
        reasons.append("bedrooms is unparseable")
    else:
        if not MIN_BEDROOMS <= bedrooms <= MAX_BEDROOMS:
            reasons.append(f"bedrooms {bedrooms} outside {MIN_BEDROOMS}-{MAX_BEDROOMS}")

    district = str(listing.get("district", ""))
    if not district_is_allowed(district):
        reasons.append(f"district {district!r} not in the allowed list")
    property_type = str(listing.get("property_type", ""))
    if property_type_is_excluded(property_type):
        reasons.append(f"property type {property_type!r} is excluded")

    floor = normalized_detail_value(listing.get("floor"))
    if not is_unknown_detail_value(floor) and floor not in ALLOWED_FLOORS:
        reasons.append(f"floor {floor!r} is not a middle/high floor")
    kitchen_type = normalized_detail_value(listing.get("kitchen_type"))
    if not is_unknown_detail_value(kitchen_type) and kitchen_type != OPEN_KITCHEN:
        reasons.append(f"kitchen type {kitchen_type!r} is not an open kitchen")

    sharing_type, sharing_terms = classify_sharing(listing)
    if sharing_type == "shared":
        reasons.append(
            f"shared rental (matched: {', '.join(sharing_terms) or 'unknown'})"
        )

    building_age = listing.get("building_age_years")
    if not is_unknown_detail_value(building_age):
        try:
            building_age_int = int(building_age)
        except (TypeError, ValueError):
            reasons.append(f"building age {building_age!r} is not a number")
        else:
            if building_age_int >= MAX_BUILDING_AGE_YEARS:
                reasons.append(
                    f"building age {building_age_int} years is not under "
                    f"{MAX_BUILDING_AGE_YEARS}"
                )

    building_area = listing.get("building_area_sqft")
    if not is_unknown_detail_value(building_area):
        try:
            building_area_int = int(building_area)
        except (TypeError, ValueError):
            reasons.append(f"building area {building_area!r} is not a number")
        else:
            if building_area_int < MIN_AREA_SQFT:
                reasons.append(
                    f"building area {building_area_int} sqft is under {MIN_AREA_SQFT}"
                )

    return reasons


def matches_filters(listing: dict[str, Any]) -> bool:
    return not filter_rejections(listing)


class IncrementalCsvWriter:

    def _sync(self) -> None:
        self.output.flush()
        try:
            os.fsync(self.output.fileno())
        except OSError as exc:
            raise RuntimeError(f"Unable to persist {self.path}: {exc}") from exc

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
                existing_rows = list(reader)
            sanitized_rows = [sanitize_row(row) for row in existing_rows]
            if sanitized_rows != existing_rows:
                temporary_path = self.path.with_name(f".{self.path.name}.tmp")
                with temporary_path.open("w", encoding="utf-8", newline="") as output:
                    writer = csv.DictWriter(output, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(sanitized_rows)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_path, self.path)
            self.existing_ids = {
                row["listing_id"] for row in sanitized_rows if row.get("listing_id")
            }

        self.output = self.path.open("a", encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.output, fieldnames=fieldnames)
        if not has_content:
            self.writer.writeheader()
            self.output.flush()
            self._sync()

    def append(self, row: dict[str, Any]) -> bool:
        listing_id = str(row["listing_id"])
        if listing_id in self.existing_ids:
            return False
        sanitized_row = sanitize_row(row)
        self.writer.writerow(
            {field: sanitized_row.get(field, "") for field in self.fieldnames}
        )
        self._sync()
        self.existing_ids.add(listing_id)
        return True

    def close(self) -> None:
        self.output.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        self.close()


class SeenSet:
    """Persistent set of every listing ID encountered on any listing page.

    Listing cards that fail the card filters are never written to the
    candidates CSV, but their IDs must still be remembered so a district can
    be skipped once an entire page of listings has already been crawled.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ids: set[str] = set()
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as source:
                self._ids.update(line.strip() for line in source if line.strip())
        self._output = self.path.open("a", encoding="utf-8")

    def add(self, listing_id: str) -> None:
        if listing_id in self._ids:
            return
        self._ids.add(listing_id)
        self._output.write(listing_id + "\n")
        self._output.flush()

    def close(self) -> None:
        self._output.close()

    def __contains__(self, listing_id: str) -> bool:
        return listing_id in self._ids

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        self.close()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"CSV input does not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as source:
        return [sanitize_row(row) for row in csv.DictReader(source)]


def scrape(
    max_pages: int | None,
    candidates_path: Path,
    page_limit: int | None = None,
) -> int:
    fetched_at = datetime.now(timezone.utc).isoformat()
    new_candidates = 0
    pages_scraped = 0
    seen_path = candidates_path.with_name(candidates_path.stem + "_seen.txt")

    with SeenSet(seen_path) as seen, IncrementalCsvWriter(
        candidates_path, CARD_FIELDS
    ) as output:
        for district_url in DISTRICT_URLS:
            if page_limit is not None and pages_scraped >= page_limit:
                break
            page = 1
            total_items: int | None = None
            stop_district = False
            while (max_pages is None or page <= max_pages) and (
                page_limit is None or pages_scraped < page_limit
            ):
                url = build_url(page, district_url)
                LOG.debug("downloading page %s: %s", page, url)
                soup = BeautifulSoup(fetch_html(url), "html.parser")
                pages_scraped += 1
                cards = soup.select("div.listItems div.property_item")
                try:
                    item_list = parse_item_list(soup)
                except RuntimeError:
                    if cards:
                        raise
                    LOG.warning("no listings in district scope: %s", district_url)
                    break

                if total_items is None:
                    total_items = int(item_list["numberOfItems"])
                    total_pages = (total_items + PAGE_SIZE - 1) // PAGE_SIZE
                    LOG.debug(
                        "candidate listings: %s; pages: %s",
                        total_items,
                        total_pages,
                    )

                unparseable_card = False
                page_unseen = 0
                for card in cards:
                    listing_id = listing_id_from_card(card)
                    if listing_id is None:
                        unparseable_card = True
                        continue
                    if listing_id not in seen:
                        page_unseen += 1
                        seen.add(listing_id)

                    listing = parse_listing(card, fetched_at)
                    if (
                        listing is not None
                        and matches_card_filters(listing)
                        and output.append(listing)
                    ):
                        new_candidates += 1

                if cards and not unparseable_card and page_unseen == 0:
                    LOG.debug(
                        "all %s listings on page %s already seen; "
                        "stopping district: %s",
                        len(cards),
                        page,
                        district_url,
                    )
                    stop_district = True

                if (
                    stop_district
                    or not cards
                    or (total_items is not None and page * PAGE_SIZE >= total_items)
                ):
                    break
                page += 1
                time.sleep(REQUEST_DELAY_SECONDS)

    return new_candidates


def write_csv(path: Path, listings: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(sanitize_row(listing) for listing in listings)


def enrich(
    candidates_path: Path,
    output_path: Path,
    cache_path: Path,
) -> int:
    if output_path.resolve() == cache_path.resolve():
        raise RuntimeError("--output and --cache must be different paths")
    candidates = read_csv_rows(candidates_path)
    new_enriched = 0
    newly_enriched: set[str] = set()
    seen_path = candidates_path.with_name(candidates_path.stem + "_enrich_seen.txt")
    with SeenSet(seen_path) as seen, IncrementalCsvWriter(
        cache_path, CSV_FIELDS
    ) as cache:
        for candidate in candidates:
            listing_id = candidate.get("listing_id", "")
            if not listing_id or listing_id in cache.existing_ids:
                continue
            if listing_id in seen:
                continue

            url = candidate.get("url", "")
            if not url:
                LOG.debug("candidate %s has no detail URL; not enriched", listing_id)
                seen.add(listing_id)
                continue

            LOG.debug("enriching listing %s: %s", listing_id, url)
            detail_soup = BeautifulSoup(fetch_html(url), "html.parser")
            seen.add(listing_id)
            if parse_json_ld(detail_soup) is None:
                LOG.debug(
                    "listing %s no longer available; not enriched "
                    "(detail URL redirected to a non-property page): %s",
                    listing_id,
                    url,
                )
                time.sleep(REQUEST_DELAY_SECONDS)
                continue
            details = parse_listing_details(detail_soup)
            if not details:
                LOG.debug(
                    "no listing details found for %s; not enriched: %s",
                    listing_id,
                    url,
                )
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            listing: dict[str, Any] = dict(candidate)
            listing.update(details)
            listing["enriched_at"] = datetime.now(timezone.utc).isoformat()
            if cache.append(listing):
                new_enriched += 1
                newly_enriched.add(listing_id)
            time.sleep(REQUEST_DELAY_SECONDS)

    enriched_rows = read_csv_rows(cache_path)
    matches: list[dict[str, Any]] = []
    for listing in enriched_rows:
        rejections = filter_rejections(listing)
        if rejections:
            listing_id = listing.get("listing_id", "")
            if listing_id in newly_enriched:
                LOG.info(
                    "%s filtered out: %s", listing.get("url", ""), "; ".join(rejections)
                )
            continue
        sharing_type, sharing_terms = classify_sharing(listing)
        listing["sharing_type"] = sharing_type
        listing["sharing_terms"] = " | ".join(sharing_terms)
        matches.append(listing)
    matches.sort(
        key=lambda item: (
            int(item["price_hkd"]),
            item["district"],
            item["listing_id"],
        )
    )
    write_csv(output_path, matches)
    return new_enriched


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
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase logging verbosity; repeat for more detail "
        "(-v info, -vv debug)",
    )
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    logging.basicConfig(
        level={
            0: logging.WARNING,
            1: logging.INFO,
        }.get(args.verbose, logging.DEBUG),
        format="%(levelname)s: %(message)s",
    )

    if args.stage in ("all", "scrape"):
        new_candidates = scrape(args.max_pages, args.candidates, args.limit)
        print(f"saved {new_candidates} new card-filtered listings to {args.candidates}")

    if args.stage in ("all", "enrich"):
        new_enriched = enrich(args.candidates, args.output, args.cache)
        print(
            f"enriched {new_enriched} new listings; "
            f"wrote matching listings to {args.output}"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "\ninterrupted; incremental results were saved",
            file=sys.stderr,
        )
        raise SystemExit(130)
