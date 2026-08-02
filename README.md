# Hong Kong apartment rental downloader

This downloader retrieves 28Hse apartment rental listings using the site's
public listing pages and applies these exact filters:

- 1 or more bedrooms
- HK$7,000 through HK$17,000 monthly rent
- 400 through 900 sqft usable area

The site only offers coarse search buckets, so the script uses those buckets
to reduce crawling and then applies the exact limits locally.

```sh
python3 -m pip install -r requirements.txt
python3 scrape_28hse.py
```

The default output is `data/28hse_rentals.csv`. Use `--max-pages 1` for a
small test run.
