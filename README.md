# CLO Tracker Website

A self-updating dashboard for 7 CLO closed-end funds: ECC, EIC, OXLC, SPMC, EARN, CCIF, PDCC.

## How it works

- **`index.html`** — the dashboard. On every load it fetches `nav-data.json` (same site,
  so no cross-origin issues) and falls back to the embedded snapshot if offline.
  Prices refresh automatically every morning via the job below; the header's
  Refresh button pulls the latest published data on demand, and an optional
  [Finnhub](https://finnhub.io) key — saved in the visitor's own browser via the
  "API key" button, never committed to this repo — enables live intraday quotes.
- **`nav-data.json`** — the dataset: fund NAV history, distributions, NII, sources,
  plus a daily price snapshot (`prices` / `prices_asof`).
- **`scrape_nav.py`** — the updater. Reads each fund's **own** sources and writes a
  new NAV only when the parse validates cleanly; refreshes prices via Finnhub
  (key from the `FINNHUB_KEY` repo secret — never in the repo or the page).
  It never guesses.
- **`.github/workflows/nav-refresh.yml`** — runs the updater daily at 09:00 UTC
  (04:00 CT, pre-market) plus manually anytime, and commits `nav-data.json` when
  something changed.

## Fund sources

| Fund | Source used by the scraper |
|------|---------------------------|
| ECC | eaglepointcreditcompany.com NAV history table |
| EIC | eaglepointincome.com NAV history table |
| SPMC | SEC 8-K (CIK 1930147) |
| PDCC | SEC 8-K (CIK 1998043) |
| CCIF | SEC 8-K (CIK 1517767) |
| OXLC | Press releases (bot-protected — manual check when unreachable) |
| EARN | Press releases (bot-protected — manual check when unreachable) |

OXLC and EARN publish NAVs only as press releases behind bot protection that blocks
datacenter IPs, so the scraper flags them for a manual check instead of writing
bad data. Everything else is fully automatic.

## Validation rules

A scraped NAV is accepted only if: the month-end date is a real month-end newer
than the stored one, and the value is within 25% of the stored NAV. Ranges are
stored as midpoints with the range recorded. `checked_on` is set only for funds
whose sources were actually read.
