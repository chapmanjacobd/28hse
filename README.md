# Hong Kong apartment rental downloader

This downloader retrieves 28Hse apartment rental listings using the site's
public listing pages and applies these filters:

- HK Island: Central, Sheung Wan, Sai Ying Pun, Shek Tong Tsui, Tin Hau, or
  Tai Hang
- Kowloon: Whampoa, Hung Hom, Tsim Sha Tsui, Jordan, Yau Ma Tei, Mong Kok,
  Prince Edward, Tai Kok Tsui, Olympic, Kowloon Station, Lai King, Mei Foo,
  Cheung Sha Wan, Lai Chi Kok, Sham Shui Po, Shek Kip Mei, Nam Cheong, Yau
  Yat Tsuen, Ho Man Tin, Kowloon Tong, San Po Kong, Wong Tai Sin, Kai Tak,
  Kowloon City, To Kwa Wan, Diamond Hill, or Lok Fu
- New Territories: Tsing Yi, Kwai Chung, Kwai Fong, Tsuen Wan, Tai Wo Hau,
  Sai Kung, or Clear Water Bay
- no village houses (村屋)
- whole-unit rentals only: listings whose title, description, or sublet marker
  contains an unambiguous shared-rental term (分租, 合租, 夾租, 租一間, 床位,
  單間, 室友, 限女/限男, 共用) are excluded
- ambiguous wording (套房, 兩房一廳, 宿舍, 包水電, 獨立水電錶, ...) does not
  exclude a listing; it is flagged on the output so it can be reviewed
- open kitchen when reported
- middle or high floor when reported
- 1, 2, or 3 bedrooms
- building age under 30 years when reported
- HK$7,000 through HK$17,000 monthly rent
- 400 through 900 sqft usable area

The scraper visits each requested district URL separately, using the site's
rent, area, bedroom buckets, and latest-first ordering to reduce crawling
before applying the exact limits locally. Every listing ID seen on any page
is recorded in a sidecar file next to the candidates CSV (e.g.
`data/28hse_candidates_seen.txt`), whether or not the card matches the
filters. Because 28Hse's ordering is not a strict newest-first sort, a
district is only stopped once an entire page contains listings that have all
been seen before, so repeated runs only look for newer listings without
dropping any.
Kitchen, floor, and building-age details are read from each listing's detail
page because server-side metadata for those fields can be incomplete. The
property type (e.g. 村屋) and the subletting marker are also read from the
detail page, since listing cards do not reliably report them.

Each output row carries a `sharing_type` (`whole`, `shared`, or `ambiguous`)
and a `sharing_terms` column listing the phrases that matched. `shared`
listings are excluded from the output; `ambiguous` listings are kept but
flagged for manual review. Negative phrases such as 不可分租 or 無需合租 are
ignored so they are not mistaken for shared-rental markers, and 適合租客 /
適合住客 do not match 合租.

For detail filters, an empty value or common unavailable placeholder such as
`--` is treated as unknown and does not exclude a listing. A known value that
conflicts with a filter still excludes it. The usable area is re-read from
each listing's detail page and checked against the area limits again, since
the area shown on a listing card can disagree with the detail page. When the
detail page omits the usable area, the building area is used as a lower bound:
a building area below the minimum excludes the listing, because the usable
area is never larger than the building area.

Scraping and detail enrichment are separate, resumable stages. The first stage
stores card-filtered candidates incrementally in
`data/28hse_candidates.csv`; the second stage incrementally stores successful
detail-page data in `data/28hse_enriched.csv` and regenerates the filtered
output. Available data is retained even when a listing omits one of the
filter-specific fields.
Enrichment attempts are also recorded in a sidecar file next to the candidates
CSV (`data/28hse_candidates_enrich_seen.txt`). Listings whose detail page has
disappeared or no longer resolves to a property are never written to the cache,
so without this file they would be re-fetched on every run; the sidecar marks
them as already attempted so they are skipped on later runs.
The detail data includes coordinates, address, description, estate, building
area, rent inclusions, cooking method, school nets, listing dates, and all
image URLs when present.

```sh
python3 -m pip install -r requirements.txt
python3 scrape_28hse.py scrape
python3 scrape_28hse.py enrich
```

The default output is `data/28hse_rentals.csv`. Running the script without a
stage runs both stages. Existing candidate and enrichment rows are skipped,
so an interrupted run can be resumed safely. Use `--limit 1` with the
`scrape` stage to fetch exactly one page total, or `--max-pages 1` to fetch
one page per district. Use `--candidates`, `--cache`, and `--output` to choose
different paths.
