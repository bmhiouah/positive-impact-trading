# Stock specs (one file per ticker)

Drop a JSON file here to add a name to the file-driven pipeline. The loader
reads every `*.json` in this folder.

Minimal shape:

```json
{
  "ticker": "AAPL",
  "enabled": true,
  "currency": "USD",
  "exchange": "SMART",
  "signal": { "entry": "m12" },
  "exits": {
    "atr_period": 20,
    "atr_multiple": 2.0,
    "target_r": null,
    "fixed_take_profit_pct": null,
    "fixed_stop_pct": null
  }
}
```

* `signal.entry` — one of `m12`, `m6`, `m3`, `trend`, `breakout`, `dip`
* `exits.atr_multiple` — stop distance in ATR units (per-stock, ATR-flexible)
* `exits.target_r` — optional R-multiple take-profit; when set, stop does not trail
* `exits.fixed_*_pct` — optional fixed percentage targets (e.g. `0.05` = +5%)
* `currency` / `exchange` — IBKR listing (default `USD` / `SMART`). European
  names must set `currency` to `EUR` (e.g. AIR); USD SMART can resolve the
  wrong company.

The live paper runner trades enabled files in this folder and builds IB
`Stock(...)` contracts from `currency`/`exchange`. Eligibility (sector / debt)
lives in `ethics/exclusions.json`, not here.
