"""Debt, profitability and asset growth from SEC EDGAR filings.

IBKR's paper account refuses fundamental requests. EDGAR publishes the
annual report. The debt ratio is what the impact screen reads. Gross
profitability and the growth of assets are stored beside it so the lab can
see them. They do not, by themselves, create a buy.

A ratio typed in the cockpit is left alone. EDGAR fills a name that has no
hand-entered ratio.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from marketmind.compliance.rules import is_whitelisted_etf
from marketmind.data.models import FundamentalData
from marketmind.universe import normalize_symbol

logger = logging.getLogger(__name__)

USER_AGENT = "Positive Impact Trading research research@example.com"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FRESH_DAYS = 7
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


@dataclass(frozen=True)
class FilingPicture:
    """What one annual report, plus the share count, says about a company."""

    symbol: str
    cik: str
    title: str
    sic: Optional[int]
    sic_description: str
    sector: Optional[str]
    total_debt: Optional[float]
    shares: Optional[float]
    gross_profitability: Optional[float]
    asset_growth: Optional[float]
    as_of: str
    error: str = ""


def _get_json(url: str, timeout: float = 30.0) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def cik_pad(value) -> str:
    return str(int(value)).zfill(10)


def ticker_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``company_tickers.json`` keyed by ticker."""
    found = {}
    for row in payload.values():
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            found[ticker] = row
    return found


def _points(facts: Dict[str, Any], tag: str, unit: str = "USD") -> List[dict]:
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if node is None and unit == "shares":
        node = facts.get("facts", {}).get("dei", {}).get(
            "EntityCommonStockSharesOutstanding"
        )
    if not node:
        return []
    return list(node.get("units", {}).get(unit, []))


def _annual(points: Iterable[dict]) -> List[Tuple[str, float]]:
    """One value per fiscal year-end, oldest first. Later filings win a tie."""
    chosen: Dict[str, dict] = {}
    for point in points:
        end = str(point.get("end") or "")
        form = str(point.get("form") or "")
        if not end or point.get("val") is None:
            continue
        annual = point.get("fp") == "FY" or form in ANNUAL_FORMS
        if not annual:
            continue
        previous = chosen.get(end)
        if previous is None or str(point.get("filed") or "") >= str(previous.get("filed") or ""):
            chosen[end] = point
    rows = sorted(chosen.items())
    return [(end, float(point["val"])) for end, point in rows]


def _latest_shares(facts: Dict[str, Any]) -> Optional[float]:
    points = _points(facts, "CommonStockSharesOutstanding", "shares")
    dated = [point for point in points if point.get("end") and point.get("val")]
    if not dated:
        return None
    latest = max(dated, key=lambda point: str(point.get("end")))
    value = float(latest["val"])
    return value if value > 0 else None


def total_debt(facts: Dict[str, Any]) -> Optional[float]:
    """Interest-bearing debt from the latest annual report.

    ``LongTermDebt`` already includes the portion due within a year at most
    filers. When that total is absent, the current and long-term pieces are
    added for the same year-end.
    """
    reported = _annual(_points(facts, "LongTermDebt"))
    if reported:
        return reported[-1][1]
    long_term = dict(_annual(_points(facts, "LongTermDebtNoncurrent")))
    current = dict(_annual(_points(facts, "LongTermDebtCurrent")))
    if not current:
        current = dict(_annual(_points(facts, "DebtCurrent")))
    ends = sorted(set(long_term) | set(current))
    if not ends:
        return None
    end = ends[-1]
    return float(long_term.get(end, 0.0) + current.get(end, 0.0))


def gross_profitability(facts: Dict[str, Any]) -> Optional[float]:
    """Gross profit divided by assets, for the latest year that has both."""
    profit = dict(_annual(_points(facts, "GrossProfit")))
    assets = dict(_annual(_points(facts, "Assets")))
    shared = sorted(set(profit) & set(assets))
    if not shared or assets[shared[-1]] <= 0:
        return None
    end = shared[-1]
    return profit[end] / assets[end]


def asset_growth(facts: Dict[str, Any]) -> Optional[float]:
    """Change in total assets over the latest two annual reports."""
    assets = _annual(_points(facts, "Assets"))
    if len(assets) < 2 or assets[-2][1] <= 0:
        return None
    return assets[-1][1] / assets[-2][1] - 1.0


def sector_from_sic(sic: Optional[int], description: str) -> Optional[str]:
    """Map a filing's industry code onto the impact screen's excluded names.

    Anything that is not on that list comes back as None. The screen then
    treats the company as an ordinary operating business.
    """
    text = (description or "").lower()
    if sic is not None and 2100 <= int(sic) <= 2199:
        return "tobacco"
    if "tobacco" in text or "cigarette" in text or "cigar" in text:
        return "tobacco"
    if any(word in text for word in ("distill", "malt beverage", "winery", "brew")):
        return "alcohol"
    if any(word in text for word in ("gambling", "casino")):
        return "gambling"
    if "adult" in text:
        return "adult_entertainment"
    if any(word in text for word in ("ordnance", "ammunition", "small arms", "missile")):
        return "weapons"
    if sic is None:
        return None
    # Banks, credit, brokers and insurers. Real-estate operators sit above this
    # range and are not treated as financial services.
    if 6000 <= int(sic) <= 6411:
        return "financial_services"
    return None


def picture_from_payloads(symbol: str, tick: Dict[str, Any],
                          submissions: Dict[str, Any],
                          facts: Dict[str, Any]) -> FilingPicture:
    sic_raw = submissions.get("sic")
    try:
        sic = int(sic_raw) if sic_raw not in (None, "") else None
    except (TypeError, ValueError):
        sic = None
    description = str(submissions.get("sicDescription") or "")
    assets = _annual(_points(facts, "Assets"))
    return FilingPicture(
        symbol=symbol.upper(),
        cik=cik_pad(tick.get("cik_str")),
        title=str(tick.get("title") or submissions.get("name") or symbol),
        sic=sic,
        sic_description=description,
        sector=sector_from_sic(sic, description),
        total_debt=total_debt(facts),
        shares=_latest_shares(facts),
        gross_profitability=gross_profitability(facts),
        asset_growth=asset_growth(facts),
        as_of=assets[-1][0] if assets else "",
    )


def fetch_picture(symbol: str, tickers: Optional[Dict[str, Any]] = None,
                  pause: float = 0.15) -> FilingPicture:
    """Download one company's latest annual facts. Raises on a network failure."""
    name = normalize_symbol(symbol)
    if tickers is None:
        tickers = ticker_map(_get_json(TICKERS_URL))
        time.sleep(pause)
    tick = tickers.get(name)
    if tick is None:
        return FilingPicture(
            symbol=name, cik="", title=name, sic=None, sic_description="",
            sector=None, total_debt=None, shares=None,
            gross_profitability=None, asset_growth=None, as_of="",
            error=f"No SEC company for {name}.",
        )
    cik = cik_pad(tick["cik_str"])
    submissions = _get_json(SUBMISSIONS_URL.format(cik=cik))
    time.sleep(pause)
    facts = _get_json(FACTS_URL.format(cik=cik), timeout=60.0)
    return picture_from_payloads(name, tick, submissions, facts)


def _ensure_table(store) -> None:
    store.conn.execute(
        """CREATE TABLE IF NOT EXISTS filing_facts (
               symbol               TEXT PRIMARY KEY,
               cik                  TEXT,
               title                TEXT,
               sic                  INTEGER,
               sic_description      TEXT,
               sector               TEXT,
               total_debt           REAL,
               shares               REAL,
               gross_profitability  REAL,
               asset_growth         REAL,
               debt_to_market_cap   REAL,
               as_of                TEXT,
               fetched_at           TEXT,
               error                TEXT
           )"""
    )
    store.conn.commit()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def last_close(store, symbol: str) -> Optional[float]:
    row = store.conn.execute(
        """SELECT close FROM price_bars
           WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1""",
        (symbol,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    price = float(row[0])
    return price if price > 0 else None


def debt_ratio(total: Optional[float], shares: Optional[float],
               price: Optional[float]) -> Optional[float]:
    if total is None or not shares or not price:
        return None
    cap = shares * price
    if cap <= 0:
        return None
    return total / cap


def _fresh(store, symbol: str) -> bool:
    row = store.conn.execute(
        "SELECT fetched_at, error FROM filing_facts WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if row is None or row[1]:
        return False
    try:
        fetched = datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return _utcnow() - fetched < timedelta(days=FRESH_DAYS)


def _save_row(store, picture: FilingPicture, ratio: Optional[float]) -> None:
    store.conn.execute(
        """INSERT INTO filing_facts
              (symbol, cik, title, sic, sic_description, sector, total_debt,
               shares, gross_profitability, asset_growth, debt_to_market_cap,
               as_of, fetched_at, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
              cik = excluded.cik, title = excluded.title, sic = excluded.sic,
              sic_description = excluded.sic_description,
              sector = excluded.sector, total_debt = excluded.total_debt,
              shares = excluded.shares,
              gross_profitability = excluded.gross_profitability,
              asset_growth = excluded.asset_growth,
              debt_to_market_cap = excluded.debt_to_market_cap,
              as_of = excluded.as_of, fetched_at = excluded.fetched_at,
              error = excluded.error""",
        (picture.symbol, picture.cik, picture.title, picture.sic,
         picture.sic_description, picture.sector, picture.total_debt,
         picture.shares, picture.gross_profitability, picture.asset_growth,
         ratio, picture.as_of, _utcnow().isoformat(), picture.error),
    )
    store.conn.commit()


def apply_to_screen(store, picture: FilingPicture, price: Optional[float]) -> str:
    """Write the debt ratio the gate reads, unless a person typed one.

    Returns ``saved``, ``kept`` or ``skipped``.
    """
    ratio = debt_ratio(picture.total_debt, picture.shares, price)
    _ensure_table(store)
    _save_row(store, picture, ratio)
    if picture.error or is_whitelisted_etf(picture.symbol):
        return "skipped"
    existing = store.get_fundamental_data(picture.symbol)
    if (existing is not None and existing.source == "COCKPIT"
            and existing.debt_to_market_cap is not None):
        return "kept"
    now = _utcnow().replace(tzinfo=None)
    sector = picture.sector
    industry = picture.sic_description or None
    if existing is not None and not sector and existing.sector:
        sector = existing.sector
    data = FundamentalData(
        symbol=picture.symbol,
        sector=sector,
        industry=industry,
        market_cap=(picture.shares * price) if picture.shares and price else None,
        total_debt=picture.total_debt,
        debt_to_market_cap=ratio,
        last_updated=now,
        source="EDGAR",
    )
    if existing is not None:
        data = existing.model_copy(update={
            "sector": sector,
            "industry": industry or existing.industry,
            "market_cap": data.market_cap if data.market_cap is not None else existing.market_cap,
            "total_debt": picture.total_debt if picture.total_debt is not None else existing.total_debt,
            "debt_to_market_cap": ratio if ratio is not None else existing.debt_to_market_cap,
            "last_updated": now,
            "source": "EDGAR",
        })
    store.upsert_fundamental_data(data)
    return "saved"


def refresh(store, symbols: Sequence[str], force: bool = False) -> Dict[str, str]:
    """Fill every name that is stale. One failure does not stop the others."""
    _ensure_table(store)
    outcome: Dict[str, str] = {}
    pending = []
    for symbol in symbols:
        name = normalize_symbol(symbol)
        if is_whitelisted_etf(name):
            outcome[name] = "etf"
            continue
        if not force and _fresh(store, name):
            outcome[name] = "fresh"
            continue
        pending.append(name)
    tickers = None
    if pending:
        try:
            tickers = ticker_map(_get_json(TICKERS_URL))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            logger.warning("SEC ticker list unavailable: %s", exc)
            for name in pending:
                outcome[name] = f"error: {exc}"
            return outcome
    for name in pending:
        try:
            picture = fetch_picture(name, tickers)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                OSError, KeyError, ValueError) as exc:
            logger.warning("Filing read failed for %s: %s", name, exc)
            picture = FilingPicture(
                symbol=name, cik="", title=name, sic=None, sic_description="",
                sector=None, total_debt=None, shares=None,
                gross_profitability=None, asset_growth=None, as_of="",
                error=str(exc),
            )
        outcome[name] = apply_to_screen(store, picture, last_close(store, name))
        time.sleep(0.15)
    return outcome


def rows(store) -> List[Dict[str, Any]]:
    _ensure_table(store)
    found = store.conn.execute(
        """SELECT symbol, sector, total_debt, debt_to_market_cap,
                  gross_profitability, asset_growth, as_of, error
           FROM filing_facts ORDER BY symbol"""
    ).fetchall()
    keys = ("symbol", "sector", "total_debt", "debt_to_market_cap",
            "gross_profitability", "asset_growth", "as_of", "error")
    return [dict(zip(keys, row)) for row in found]
