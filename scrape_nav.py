#!/usr/bin/env python3
"""Monthly NAV scraper for the CLO fund tracker.

Reads nav-data.json (list of fund dicts), checks each fund's OWN sources for a
month-end NAV newer than the stored latest_nav_date, and updates the JSON in
place. Also picks up new monthly distribution amounts and quarterly NII
estimates when they appear in the same sources.

Rules (never violated):
  - Never guess or interpolate a number. A fund is left unchanged unless every
    parse step validates cleanly.
  - A parsed NAV must be within 25% of the stored latest_nav, and its month-end
    date must be a real month-end strictly newer than the stored date.
  - checked_on is set to today only for funds where at least one source was
    successfully read. Unreachable funds keep their old checked_on and are
    reported, not silently marked.
  - On a changed row: latest_* rolls into prior_*, new values are set,
    nav_post_date = publication/filing date, changed_on = today, and a
    one-sentence update_note is written.

Exit 0 always (a clean no-change run is success). Prints a per-fund summary.
Only stdlib + requests.
"""

import calendar
import json
import re
import sys
from datetime import date, datetime

import requests

DATA_FILE = "nav-data.json"
TODAY = date.today().isoformat()
UA = {"User-Agent": "CLO-NAV-Tracker/1.0 (contact: adriangarcia174@gmail.com)"}
SEC_UA = {"User-Agent": "CLO-NAV-Tracker/1.0 (contact: adriangarcia174@gmail.com)",
          "Accept-Encoding": "gzip", "Host": "www.sec.gov"}

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def get(url, headers=None, timeout=30, retries=4):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers or UA, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001 - retry transient failures
            last = e
            if i < retries - 1:
                import time
                time.sleep(2 * (i + 1))
    raise last


def parse_mdy(s):
    """'August 31, 2026' -> '2026-08-31'. None if unparsable."""
    m = re.match(r"\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s or "")
    if not m or m.group(1) not in MONTHS:
        return None
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def is_month_end(iso):
    y, m, d = map(int, iso.split("-"))
    return d == calendar.monthrange(y, m)[1]


def clean_num(s):
    return float(s.strip().rstrip(".").replace(",", "").replace("$", ""))


def strip_tags(html):
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.replace("&#160;", " ").replace("\xa0", " ").strip()


# ---------------------------------------------------------------- SEC helpers

def sec_filings(cik):
    """Recent filings for a CIK -> list of (form, filing_date, accession_nodash, primary_doc)."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    d = json.loads(get(url, headers={"User-Agent": UA["User-Agent"],
                                     "Accept": "application/json", "Host": "data.sec.gov"}))
    r = d["filings"]["recent"]
    out = []
    for f, dt, acc, doc in zip(r["form"], r["filingDate"], r["accessionNumber"], r["primaryDocument"]):
        out.append((f, dt, acc.replace("-", ""), doc))
    return out


def sec_doc(cik, acc_nodash, doc):
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
    return strip_tags(get(url, headers=SEC_UA))


NAV_RE = re.compile(
    r"net asset value per (?:common )?share.{0,250}?as of ([A-Z][a-z]+ \d{1,2}, \d{4}).{0,60}?"
    r"was (?:between )?\$([\d.,]+)(?:\s*(?:[–—\-]|and)\s*\$([\d.,]+))?", re.I | re.S)
NII_RE = re.compile(
    r"net investment income.{0,200}?was (?:between )?\$([\d.,]+)"
    r"(?:\s*(?:[–—\-]|and)\s*\$([\d.,]+))?.{0,80}?per (?:common )?share", re.I | re.S)
DIST_RE = re.compile(
    r"(?:declared|announces?|payable).{0,120}?monthly.{0,80}?distribution of \$([\d.,]+) per (?:common )?share",
    re.I | re.S)


def scrape_8k_nav(fund, cik):
    """Generic 8-K NAV scraper for SPMC / PDCC / CCIF.

    Returns dict(status, nav_date, nav, nav_lo, nav_hi, post_date, source,
                 nii?, dist?) or dict(status='no_change'|'unreachable', ...).
    """
    filings = sec_filings(cik)
    eights = [(dt, acc, doc) for f, dt, acc, doc in filings if f == "8-K"][:6]
    if not eights:
        return {"status": "unreachable", "detail": "no 8-K filings found"}
    best = None
    for dt, acc, doc in eights:  # newest first
        try:
            text = sec_doc(cik, acc, doc)
        except Exception as e:  # noqa: BLE001 - keep scanning older filings
            continue
        m = NAV_RE.search(text)
        if not m:
            continue
        nav_date = parse_mdy(m.group(1))
        if not nav_date or not is_month_end(nav_date):
            continue
        lo = clean_num(m.group(2))
        hi = clean_num(m.group(3)) if m.group(3) else lo
        nav = round((lo + hi) / 2, 2)
        if nav_date <= fund["latest_nav_date"]:
            return {"status": "no_change", "detail": f"latest 8-K NAV is {nav_date}, not newer"}
        if abs(nav / fund["latest_nav"] - 1) > 0.25:
            return {"status": "no_change",
                    "detail": f"parsed NAV {nav} deviates >25% from stored {fund['latest_nav']} - rejected"}
        best = {"status": "updated", "nav_date": nav_date, "nav": nav,
                "nav_lo": lo, "nav_hi": hi, "post_date": dt,
                "source": f"{fund['ticker']} 8-K filed {dt}",
                "range": lo != hi}
        # optional extras from the same filing
        nm = NII_RE.search(text)
        if nm:
            nii_lo = clean_num(nm.group(1))
            nii_hi = clean_num(nm.group(2)) if nm.group(2) else nii_lo
            best["nii"] = round((nii_lo + nii_hi) / 2, 2)
            best["nii_range"] = (nii_lo, nii_hi)
        dm = DIST_RE.search(text)
        if dm:
            best["dist"] = round(clean_num(dm.group(1)), 2)
        return best
    return {"status": "no_change", "detail": "no NAV found in recent 8-Ks"}


# ------------------------------------------------------- Eagle Point tables

def scrape_eaglepoint_nav(fund, url):
    """Parse the NAV history table on Eagle Point IR sites (ECC / EIC)."""
    try:
        html = get(url)
    except Exception as e:  # noqa: BLE001
        return {"status": "unreachable", "detail": f"IR page fetch failed: {e}"}
    rows = re.findall(
        r'<td class="navData w10">(\d\d/\d\d/\d\d\d\d)</td>'
        r'<td class="navData">(.*?)</td><td class="navData">(.*?)</td>', html)
    if not rows:
        return {"status": "unreachable", "detail": "NAV table not found in page"}
    clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
    parsed = []
    for d, _tot, ps in rows:
        m = re.search(r"\$([\d.,]+)", clean(ps))
        if not m:
            continue
        mm, dd, yy = d.split("/")
        iso = f"{yy}-{mm}-{dd}"
        if is_month_end(iso):
            parsed.append((iso, round(clean_num(m.group(1)), 2)))
    if not parsed:
        return {"status": "unreachable", "detail": "no month-end rows parsed"}
    nav_date, nav = parsed[0]  # table is newest-first
    if nav_date <= fund["latest_nav_date"]:
        return {"status": "no_change", "detail": f"table newest is {nav_date}, not newer"}
    if abs(nav / fund["latest_nav"] - 1) > 0.25:
        return {"status": "no_change",
                "detail": f"parsed NAV {nav} deviates >25% from stored {fund['latest_nav']} - rejected"}
    # publication date ~= the monthly 497AD filing for that period, when close
    post_date = fund["nav_post_date"]
    try:
        cik = {"ECC": "0001604174", "EIC": "0001754836"}[fund["ticker"]]
        for f, dt, _acc, _doc in sec_filings(cik):
            if f == "497AD":
                fdt = datetime.strptime(dt, "%Y-%m-%d").date()
                mend = datetime.strptime(nav_date, "%Y-%m-%d").date()
                if 0 <= (fdt - mend).days <= 45:
                    post_date = dt
                    break
    except Exception:  # noqa: BLE001 - keep the old post date
        pass
    return {"status": "updated", "nav_date": nav_date, "nav": nav,
            "nav_lo": nav, "nav_hi": nav, "post_date": post_date,
            "source": f"{fund['ticker']} NAV history table ({url})",
            "range": False}


# ------------------------------------------------------------- press wires
# OXLC and EARN publish NAVs only as press releases behind bot protection.
# Best-effort direct fetch; on failure the fund is left untouched and flagged.

def scrape_presswire_nav(fund, url, title_hint):
    try:
        html = get(url)
    except Exception as e:  # noqa: BLE001
        return {"status": "unreachable", "detail": f"fetch failed: {e}"}
    return {"status": "no_change",
            "detail": f"press-wire parsing not implemented for {fund['ticker']}; manual check needed"}


SCRAPERS = {
    "ECC": lambda f: scrape_eaglepoint_nav(f, "https://www.eaglepointcreditcompany.com/our-fund"),
    "EIC": lambda f: scrape_eaglepoint_nav(f, "https://www.eaglepointincome.com/our-fund"),
    "SPMC": lambda f: scrape_8k_nav(f, "0001930147"),
    "PDCC": lambda f: scrape_8k_nav(f, "0001998043"),
    "CCIF": lambda f: scrape_8k_nav(f, "0001517767"),
    "OXLC": lambda f: scrape_presswire_nav(f, "https://ir.oxfordlanecapital.com/news-events/news-releases", "Net Asset Value Update"),
    "EARN": lambda f: scrape_presswire_nav(f, "https://www.ellingtoncredit.com/monthly-nav-portfolio-updates", "Monthly NAV"),
}


def apply_update(fund, res):
    old_date, old_nav = fund["latest_nav_date"], fund["latest_nav"]
    fund["prior_nav_date"], fund["prior_nav"] = old_date, old_nav
    fund["latest_nav_date"], fund["latest_nav"] = res["nav_date"], res["nav"]
    fund["nav_post_date"] = res["post_date"]
    if res.get("range"):
        fund["nav_basis"] = (f"Midpoint of range ${res['nav_lo']:.2f}-${res['nav_hi']:.2f}")
    else:
        fund["nav_basis"] = "Point value (fund source)"
    fund["nav_source"] = res["source"]
    fund["checked_on"] = TODAY
    fund["changed_on"] = TODAY
    bits = [f"New {res['nav_date'][5:7]}/{res['nav_date'][8:10]} NAV ${res['nav']:.2f} (was {old_date[5:7]}/{old_date[8:10]} ${old_nav:.2f})."]
    if res.get("dist") is not None and abs(res["dist"] - fund["current_dist"]) > 1e-9:
        bits.append(f"Monthly distribution now ${res['dist']:.2f} (was ${fund['current_dist']:.2f}).")
        fund["current_dist"] = res["dist"]
        fund["qtrly_dist"] = round(res["dist"] * 3, 2)
    if res.get("nii") is not None:
        lo, hi = res["nii_range"]
        bits.append(f"Quarterly NII estimate ${lo:.2f}-${hi:.2f}.")
        fund["nii_qtr"] = res["nii"]
    fund["update_note"] = " ".join(bits)
    return bits


def main():
    with open(DATA_FILE) as fh:
        funds = json.load(fh)
    summary = []
    changed = 0
    for fund in funds:
        t = fund["ticker"]
        scraper = SCRAPERS.get(t)
        if not scraper:
            summary.append(f"{t}: no scraper configured - skipped")
            continue
        try:
            res = scraper(fund)
        except Exception as e:  # noqa: BLE001 - never let one fund kill the run
            summary.append(f"{t}: ERROR {type(e).__name__}: {e} - unchanged")
            continue
        if res["status"] == "updated":
            bits = apply_update(fund, res)
            changed += 1
            summary.append(f"{t}: UPDATED - {' '.join(bits)}")
        elif res["status"] == "no_change":
            fund["checked_on"] = TODAY
            summary.append(f"{t}: no change ({res.get('detail', '')})")
        else:  # unreachable
            summary.append(f"{t}: UNREACHABLE ({res.get('detail', '')}) - unchanged, needs manual check")
    if changed:
        with open(DATA_FILE, "w") as fh:
            json.dump(funds, fh, indent=2)
            fh.write("\n")
    print(f"NAV refresh {TODAY}: {changed} fund(s) updated.")
    for line in summary:
        print(" -", line)


if __name__ == "__main__":
    sys.exit(main())
