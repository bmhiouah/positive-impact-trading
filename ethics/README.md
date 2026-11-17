# Shared ethics / exclusion filter

`exclusions.json` is the **single source of truth** for Impact eligibility:

* excluded business sectors (canonical id + aliases for IB / UI labels)
* max debt-to-market-cap (default 33%)
* whitelisted impact ETFs and spot metals

Compliance (`marketmind.compliance.rules.is_excluded_sector`) loads this file.
Do not hardcode a parallel exclusion list in strategies or UI.
