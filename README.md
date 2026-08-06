# Hong Kong apartment downloader (rent or buy)

This downloader retrieves 28Hse apartment listings using the site's public
listing pages and applies these filters to rentals (`--mode rent`, the
default):

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
- 350 through 900 sqft usable area

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
Email addresses, phone-like values (including Chinese-numeral and full-width
digits), and labeled WeChat, Instagram, or WhatsApp usernames are removed from
text fields before rows are written. Existing tracked CSV rows have also been
scrubbed.

```sh
python3 -m pip install -r requirements.txt
python3 scrape_28hse.py -v

git diff data/28hse_rentals.csv | xsv select 14
```

The default output is `data/28hse_rentals.csv`. Running the script without a
stage runs both stages. Existing candidate and enrichment rows are skipped,
so an interrupted run can be resumed safely. Use `--limit 1` with the
`scrape` stage to fetch exactly one page total, or `--max-pages 1` to fetch
one page per district. Use `--candidates`, `--cache`, and `--output` to choose
different paths.

## Buy mode

`--mode buy` crawls sales listings instead of rentals. It reuses the same
district, area, bedroom, floor, kitchen, building-age, and property-type
filters, but targets a sale price of HK$2,000,000 through HK$12,000,000 and
parses the `售 $798 萬元` / `售 $1.3 億元` card prices into HKD. Its default
files are `data/28hse_buy_candidates.csv`, `data/28hse_buy_enriched.csv`, and
`data/28hse_buy.csv`. The rent-specific fields (`rent_includes`,
`subletting`, and the shared-rental classification) are left empty in buy
mode.

Because management fees ("HOA-like" holding costs) are not exposed on 28Hse,
the cost model in `hk_costs.py` estimates them. Buy mode computes each
listing's estimated monthly outlay (mortgage plus rates, management fee, and
maintenance) and skips listings whose outlay exceeds
`--max-monthly-outlay` (default HK$35,000) before they are enriched, so
unaffordable flats never cost a detail-page fetch. `hk_costs.py` also drives
the extra output columns, a 30-year buy-vs-rent comparison in the spirit of
`~/bin/condo.py`:

- `monthly_mortgage_hkd`, `monthly_holding_hkd`, `monthly_outlay_hkd`,
  `initial_outlay_hkd`, `est_market_rent_hkd`
- `npv_total_cost_buy_30y_hkd`, `npv_total_cost_rent_30y_hkd`
- `npv_net_worth_buy_30y_hkd`, `npv_net_worth_rent_30y_hkd`,
  `buy_vs_rent_30y_hkd`

Both scenarios start from the same liquid capital and the same monthly
housing budget, the flat's estimated market rent (price times the assumed
rental yield). The renter spends the whole budget on rent; the buyer pays the
mortgage (default 25 years at 3.75%, 30% down payment) plus holding costs and
invests or draws the difference, while the home appreciates. Cash values are
deflated by inflation to today's dollars. Assumptions can be overridden, e.g.:

```sh
python3 scrape_28hse.py --mode buy --cost-down-payment 0.20 \
    --cost-mortgage-rate 0.04 --max-monthly-outlay 40000
```

Run `python3 scrape_28hse.py --help` for the full list of `--cost-*` flags.

Progress and diagnostic messages go through the `logging` module to stderr.
By default only warnings are shown; `-v` additionally shows info messages
(e.g. why a newly enriched listing was filtered out of the output CSV) and
`-vv` also shows debug messages (e.g. each page downloaded, and why a listing
could not be enriched).
