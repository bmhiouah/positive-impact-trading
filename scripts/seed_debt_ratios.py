#!/usr/bin/env python3
"""Seed debt_to_market_cap into fundamental_data for paper compliance.

IB paper accounts cannot fetch fundamentals (error 10358). Equities without a
ratio are blocked at the impact gate. Run once (or after wiping the DB):

    python scripts/seed_debt_ratios.py

Reads ethics/debt_ratios.json and writes via MarketDataService.set_fundamental
so cockpit-sourced rows are preserved on later IB contract syncs. Does not
connect to IBKR and does not place orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marketmind import config
from marketmind.data import DataStore
from marketmind.market_data import MarketDataService
from marketmind.universe import Universe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path", default=config.DB_PATH,
        help="SQLite path (default: %(default)s)",
    )
    parser.add_argument(
        "--ratios",
        default=str(ROOT / "ethics" / "debt_ratios.json"),
        help="JSON file with a ratios object",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print planned writes without updating the DB",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.ratios).read_text(encoding="utf-8"))
    ratios = {str(k).upper(): float(v) for k, v in (payload.get("ratios") or {}).items()}
    if not ratios:
        print("No ratios found in", args.ratios, file=sys.stderr)
        return 1

    store = DataStore(args.db_path)
    market = MarketDataService(store, Universe(store))
    written = 0
    for symbol, ratio in sorted(ratios.items()):
        if ratio < 0 or ratio >= 1:
            print(f"skip {symbol}: ratio {ratio} looks invalid")
            continue
        existing = store.get_fundamental_data(symbol)
        before = existing.debt_to_market_cap if existing else None
        print(f"{symbol}: {before!r} -> {ratio}")
        if not args.dry_run:
            market.set_fundamental(symbol, debt_to_market_cap=ratio)
            written += 1
    store.close()
    print(f"{'Would write' if args.dry_run else 'Wrote'} {written or len(ratios)} ratios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
