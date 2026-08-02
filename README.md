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
coarse search buckets to reduce crawling before applying the exact limits
locally. Kitchen, floor, and building-age details are read from each
listing's detail page.

```sh
python3 -m pip install -r requirements.txt
python3 scrape_28hse.py
```

The default output is `data/28hse_rentals.csv`. Use `--max-pages 1` for a
small test run.
