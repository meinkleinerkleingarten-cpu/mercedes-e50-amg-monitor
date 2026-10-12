# E 50 AMG W210 international search bot

Daily discovery of 1996 and 1997 E 50 AMG listings, separately grouped by country/year. Uses public Bing RSS search discovery, **not** unauthorized crawling of classified sites. Search results are *candidates*, not verified live offers.

## Start

`python bot.py`

Run on GitHub Actions by uploading this folder to a repository and enabling Actions. Workflow runs daily at 05:15 UTC (07:15 CEST / 06:15 CET), or on demand. No deployment has been performed.

## Outputs

- `candidates.csv`: candidate URLs with country, year, and extracted price (if reliably available).
- `listings.sqlite3`: candidates and dated observations.
- `country_summary.csv`: country/year summary **only for verified live listings**.

## Verification

Review candidates by opening each source URL. Check original E 50 AMG W210, year, active offer, asking price, listing country, and duplicate VIN/photographs. Then set `status='verified_live'` in the database for eligible listings. Run bot again to refresh summary. Mark ended sales `sold` or `inactive`; do not mix auctions or sold prices into asking-price statistics. Search snippets can be outdated. Automated live-status validation requires official APIs or permission from each portal.

## FX rates

Prices in non-EUR currencies are intentionally excluded from EUR averages unless rates are explicitly supplied via `FX_EUR_JSON`, e.g. `FX_EUR_JSON='{"SEK":0.09,"USD":0.85}' python bot.py`. Example values are NOT live rates. For a production bot, fetch dated FX rates from the ECB or another documented provider.

## Limitations

RSS indexing is incomplete and some sites may block results. This is a starter implementation, not exhaustive worldwide coverage. Year is inferred from result snippets and can mean first registration rather than production year. Prices are parsed conservatively and require human review. No automatic cross-site VIN deduplication yet. GitHub Actions schedule is best effort, not guaranteed exact execution time.

## HTML dashboard and manual review (v2)

Run `python bot.py && python dashboard.py` and open `dashboard.html` locally in a browser. The dashboard provides country/year means, medians, ranges, candidate filtering, source links and dated price observations. **It does not claim unverified results are live.** The GitHub Actions workflow regenerates the HTML every day and commits it with the database. To publish it online, enable GitHub Pages separately and configure its source appropriately; no Pages deployment is performed automatically.

To confirm an offer, open its original URL and inspect it manually, then obtain its `id` from SQLite (`SELECT id,title,url FROM listings`) and run `python review.py LISTING_ID verified_live` (optional `--vin WDB...`). Re-run `python dashboard.py`. Other supported statuses: `inactive`, `sold`, `duplicate`, `rejected`, `unverified`. Matching confirmed VINs are flagged during review. **This is not automated live checking**: reliable live-status checks depend on authorized portal APIs or permission. A search result appearing again is not proof that an ad is active. The dashboard's historical plot shows observations for currently verified listings, not a historic verified-active universe. Portal coverage remains incomplete and listings may go stale. Dates are from discovery and do not establish production year.
