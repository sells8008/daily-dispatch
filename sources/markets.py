"""Markets source — glance-bar ticks. No API key required.

BTC via CoinGecko (stable, reliable). S&P 500 via Yahoo Finance's unofficial
chart endpoint — Stooq's documented free CSV endpoint (the one named in the
build spec) has since been retired/bot-walled, so this substitutes the
commonly-used Yahoo fallback. 10Y yield is skipped per the spec's own
"omit if flaky" allowance — there's no equally simple free source for it.

Both quotes are independently fail-soft: if one is down, the other still
shows, and the glance-bar cell degrades to "unavailable" only if both fail.
"""

import logging

import requests

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"

# Yahoo's chart endpoint 429s without a browser-like User-Agent.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _fetch_btc(timeout=10):
    try:
        resp = requests.get(
            COINGECKO_URL,
            params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=timeout,
        )
        resp.raise_for_status()
        change = resp.json()["bitcoin"]["usd_24h_change"]
        return {"pct": round(abs(change), 1), "up": change >= 0}
    except Exception:
        logger.exception("BTC quote failed")
        return None


def _fetch_sp500(timeout=10):
    try:
        resp = requests.get(YAHOO_CHART_URL, headers=BROWSER_HEADERS, timeout=timeout)
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev_close = meta["previousClose"]
        change_pct = (price - prev_close) / prev_close * 100
        return {"pct": round(abs(change_pct), 1), "up": change_pct >= 0}
    except Exception:
        logger.exception("S&P 500 quote failed")
        return None


def fetch():
    sp500 = _fetch_sp500()
    btc = _fetch_btc()
    if sp500 is None and btc is None:
        return None
    return {"sp500": sp500, "btc": btc, "y10": None}
