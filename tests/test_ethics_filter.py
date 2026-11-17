"""Shared ethics / exclusion filter: file load and sector alias matching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketmind.compliance.rules import is_excluded_sector
from marketmind.ethics.filter import (
    ethics_filter_from_payload,
    load_ethics_filter,
    normalize_sector_label,
)


def test_normalize_sector_label_collapses_punctuation() -> None:
    assert normalize_sector_label("  Financial Services  ") == "financial_services"
    assert normalize_sector_label("Aerospace & Defense") == "aerospace_defense"
    assert normalize_sector_label("financial_services") == "financial_services"


def test_default_file_loads_canonical_ids() -> None:
    filt = load_ethics_filter()
    assert "financial_services" in filt.excluded_sector_ids
    assert "weapons" in filt.excluded_sector_ids
    assert filt.max_debt_to_market_cap == pytest.approx(0.33)
    assert "SPUS" in filt.whitelisted_etfs


@pytest.mark.parametrize(
    "label",
    [
        "financial_services",
        "Financial Services",
        "Financials",
        "Banks",
        "  banking ",
        "Insurance",
    ],
)
def test_financial_aliases_are_excluded(label: str) -> None:
    assert is_excluded_sector(label) is True


@pytest.mark.parametrize(
    "label",
    [
        "Aerospace & Defense",
        "defense",
        "Weapons",
        "Casinos and Gaming",
        "Tobacco Products",
        "Alcoholic Beverages",
    ],
)
def test_other_ib_style_aliases_are_excluded(label: str) -> None:
    assert is_excluded_sector(label) is True


@pytest.mark.parametrize(
    "label",
    ["Technology", "technology", "Consumer Discretionary", "Health Care", None],
)
def test_non_excluded_sectors_pass(label: str | None) -> None:
    assert is_excluded_sector(label) is False


def test_custom_filter_payload(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "max_debt_to_market_cap": 0.25,
        "excluded_sectors": [
            {"id": "tobacco", "aliases": ["Tobacco", "cigars"]},
        ],
        "whitelisted_etfs": ["SPUS"],
        "whitelisted_metals": ["GOLD"],
    }
    path = tmp_path / "exclusions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    filt = load_ethics_filter(path)
    assert filt.is_excluded_sector("Cigars") is True
    assert filt.is_excluded_sector("technology") is False
    assert filt.is_debt_ratio_compliant(0.20) is True
    assert filt.is_debt_ratio_compliant(0.25) is False
    assert filt.is_whitelisted_etf("spus") is True


def test_ethics_filter_from_payload_builds_alias_map() -> None:
    filt = ethics_filter_from_payload(
        {
            "excluded_sectors": [
                {"id": "gambling", "aliases": ["Casinos", "Gaming"]},
            ],
            "max_debt_to_market_cap": 0.33,
            "whitelisted_etfs": [],
            "whitelisted_metals": [],
        }
    )
    assert filt.matched_sector_id("casinos") == "gambling"
    assert filt.matched_sector_id("technology") is None
