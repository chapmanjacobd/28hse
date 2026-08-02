# Hong Kong apartment rental downloader

This downloader retrieves 28Hse apartment rental listings using the site's
public listing pages and applies these exact filters:

- HK Island: Central, Sheung Wan, Sai Ying Pun, Shek Tong Tsui, Tin Hau, or
  Tai Hang
- Kowloon: Whampoa, Hung Hom, Tsim Sha Tsui, Jordan, Yau Ma Tei, Mong Kok,
  Prince Edward, Tai Kok Tsui, Olympic, Kowloon Station, Lai King, Mei Foo,
  Cheung Sha Wan, Lai Chi Kok, Sham Shui Po, Shek Kip Mei, Nam Cheong, Yau
  Yat Tsuen, Ho Man Tin, Kowloon Tong, San Po Kong, Wong Tai Sin, Kai Tak,
  Kowloon City, To Kwa Wan, Diamond Hill, or Lok Fu
- New Territories: Tsing Yi, Kwai Chung, Kwai Fong, Tsuen Wan, Tai Wo Hau,
  Sai Kung, or Clear Water Bay
- open kitchen
- middle or high floor
- 1, 2, or 3 bedrooms
- building age under 30 years
- HK$7,000 through HK$17,000 monthly rent
- 400 through 900 sqft usable area

The scraper visits each requested district URL separately, using the site's
rent, area, and bedroom buckets to reduce crawling before applying the exact
limits locally. Kitchen, floor, and building-age details are read from each
listing's detail page because server-side metadata for those fields can be
incomplete.

Scraping and detail enrichment are separate, resumable stages. The first stage
stores card-filtered candidates incrementally in
`data/28hse_candidates.csv`; the second stage incrementally stores successful
detail-page data in `data/28hse_enriched.csv` and regenerates the filtered
output. Available data is retained even when a listing omits one of the
filter-specific fields.
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
