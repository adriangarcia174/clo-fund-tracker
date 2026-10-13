# CLO Fund Tracker

A self-updating dashboard for 7 CLO closed-end funds: ECC, EIC, OXLC, SPMC, EARN, CCIF, PDCC.

## How it works

- **`index.html`** — the dashboard. On every load it fetches `nav-data.json` (same site,
  so no cross-origin issues) and falls back to the embedded snapshot if offline.
  Live price refresh uses a free [Finnhub](https://finnhub.io) API key stored in the
  visitor's own browser (paste once via the "API key" button) — no key is committed
  to this repo.
- **`nav-data.json`** — the fund dataset: NAV history, distributions, NII, sources.
- **`scrape_nav.py`** — the monthly updater. Reads each fund's **own** sources and
  writes a new NAV only when the parse validates cleanly. It never guesses.
- **`.github/workflows/nav-refresh.yml`** — runs the scraper on the 18th of each
  month (plus manually anytime) and commits `nav-data.json` when something changed.

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
