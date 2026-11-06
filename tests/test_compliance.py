"""Unit tests for the Impact-compliant execution engine.

These tests verify that every constraint from the Project Constraints
document is hardcoded into the execution engine and cannot be bypassed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from marketmind.compliance.engine import (
    ExecutionDecision,
    ImpactExecutionEngine,
    route_order,
)
from marketmind.compliance.redemption import (
    InterestRecord,
    InterestRedemptionLedger,
    record_interest_income,
)
from marketmind.compliance.rules import (
    AccountType,
    AssetClass,
    ComplianceVerdict,
    EXCLUDED_SECTORS,
    MAX_DEBT_TO_MARKET_CAP,
    OrderIntent,
    OrderSide,
    ScreeningResult,
    is_debt_ratio_compliant,
    is_excluded_sector,
    is_whitelisted_etf,
    is_whitelisted_metal,
)
from marketmind.compliance.screener import ImpactScreener, screen_symbol
from marketmind.compliance.validator import (
    OrderValidator,
    validate_order_intent,
)


def make_intent(symbol: str = "AAPL",
                asset_class: AssetClass = AssetClass.EQUITY,
                side: OrderSide = OrderSide.BUY,
                account_type: AccountType = AccountType.CASH,
                sector: str = "technology",
                debt_to_market_cap: float = 0.10,
                is_impact_etf: bool = False,
                is_spot_metal: bool = False,
                quantity: float = 10.0) -> OrderIntent:
    """Helper to build a compliant OrderIntent by default."""
    return OrderIntent(
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        quantity=quantity,
        account_type=account_type,
        sector=sector,
        debt_to_market_cap=debt_to_market_cap,
        is_impact_etf=is_impact_etf,
        is_spot_metal=is_spot_metal,
    )


# ---------------------------------------------------------------------------
# rules.py tests
# ---------------------------------------------------------------------------


class TestWhitelistPredicates:
    def test_is_whitelisted_etf(self):
        assert is_whitelisted_etf("SPUS") is True
        assert is_whitelisted_etf("hlal") is True  # case-insensitive
        assert is_whitelisted_etf("AAPL") is False
        assert is_whitelisted_etf("QQQ") is False

    def test_is_whitelisted_metal(self):
        assert is_whitelisted_metal("XAUUSD") is True
        assert is_whitelisted_metal("xagusd") is True
        assert is_whitelisted_metal("AAPL") is False

    def test_is_excluded_sector(self):
        assert is_excluded_sector("financial_services") is True
        assert is_excluded_sector("alcohol") is True
        assert is_excluded_sector("gambling") is True
        assert is_excluded_sector("adult_entertainment") is True
        assert is_excluded_sector("tobacco") is True
        assert is_excluded_sector("weapons") is True
        assert is_excluded_sector("technology") is False
        assert is_excluded_sector(None) is False
        assert is_excluded_sector("  Financial_Services  ") is True

    def test_debt_ratio_compliant(self):
        assert is_debt_ratio_compliant(0.10) is True
        assert is_debt_ratio_compliant(0.33) is False  # boundary: under 33%, not equal
        assert is_debt_ratio_compliant(0.331) is False
        assert is_debt_ratio_compliant(0.50) is False
        assert is_debt_ratio_compliant(None) is False

    def test_max_debt_threshold_is_33_percent(self):
        assert MAX_DEBT_TO_MARKET_CAP == Decimal("0.33") or abs(
            MAX_DEBT_TO_MARKET_CAP - 0.33
        ) < 1e-9


class TestExcludedSectors:
    def test_all_required_sectors_present(self):
        required = {
            "financial_services", "alcohol", "gambling",
            "adult_entertainment", "tobacco", "weapons",
        }
        assert required.issubset(EXCLUDED_SECTORS)


# ---------------------------------------------------------------------------
# screener.py tests
# ---------------------------------------------------------------------------


class TestImpactScreener:
    def test_compliant_equity_passes(self):
        result = screen_symbol(
            symbol="AAPL",
            asset_class=AssetClass.EQUITY,
            sector="technology",
            debt_to_market_cap=0.15,
        )
        assert result.passed is True
        assert result.reasons == []

    def test_excluded_sector_blocks(self):
        for sector in EXCLUDED_SECTORS:
            result = screen_symbol(
                symbol="BADCO",
                asset_class=AssetClass.EQUITY,
                sector=sector,
                debt_to_market_cap=0.10,
            )
            assert result.passed is False, f"Sector {sector} should be blocked"
            assert any("excluded" in r.lower() for r in result.reasons)

    def test_debt_ratio_over_limit_blocks(self):
        result = screen_symbol(
            symbol="LEVCO",
            asset_class=AssetClass.EQUITY,
            sector="technology",
            debt_to_market_cap=0.50,
        )
        assert result.passed is False
        assert any("exceeds" in r.lower() for r in result.reasons)

    def test_debt_ratio_at_boundary_blocks(self):
        result = screen_symbol(
            symbol="BOUNDCO",
            asset_class=AssetClass.EQUITY,
            sector="technology",
            debt_to_market_cap=0.33,
        )
        assert result.passed is False

    def test_missing_debt_data_blocks(self):
        result = screen_symbol(
            symbol="NODATA",
            asset_class=AssetClass.EQUITY,
            sector="technology",
            debt_to_market_cap=None,
        )
        assert result.passed is False
        assert any("no debt-to-market-cap data" in r.lower() for r in result.reasons)

    def test_whitelisted_etf_passes(self):
        for etf in ("SPUS", "HLAL", "UMMA", "ISUS"):
            result = screen_symbol(
                symbol=etf,
                asset_class=AssetClass.ETF,
                is_impact_etf=True,
            )
            assert result.passed is True, f"ETF {etf} should pass"

    def test_non_whitelisted_etf_blocks(self):
        result = screen_symbol(
            symbol="QQQ",
            asset_class=AssetClass.ETF,
            is_impact_etf=False,
        )
        assert result.passed is False
        assert any("whitelist" in r.lower() for r in result.reasons)

    def test_whitelisted_metal_passes(self):
        for metal in ("XAUUSD", "XAGUSD", "GOLD", "SILVER"):
            result = screen_symbol(
                symbol=metal,
                asset_class=AssetClass.SPOT_METAL,
                is_spot_metal=True,
            )
            assert result.passed is True, f"Metal {metal} should pass"

    def test_non_whitelisted_metal_blocks(self):
        result = screen_symbol(
            symbol="COPPER",
            asset_class=AssetClass.SPOT_METAL,
            is_spot_metal=True,
        )
        assert result.passed is False
        assert any("whitelist" in r.lower() for r in result.reasons)

    def test_screener_with_lookup_table(self):
        screener = ImpactScreener(
            sector_lookup={"MSFT": "technology"},
            ratio_lookup={"MSFT": 0.05},
        )
        result = screener.screen(
            symbol="MSFT",
            asset_class=AssetClass.EQUITY,
        )
        assert result.passed is True
        assert result.sector == "technology"
        assert result.debt_to_market_cap == 0.05


# ---------------------------------------------------------------------------
# validator.py tests
# ---------------------------------------------------------------------------


class TestOrderValidator:
    def test_compliant_equity_buy_approved(self):
        verdict = validate_order_intent(make_intent(symbol="AAPL", side=OrderSide.BUY))
        assert verdict.approved is True
        assert verdict.blocked_reasons == []

    def test_compliant_equity_sell_close_approved(self):
        verdict = validate_order_intent(make_intent(symbol="AAPL", side=OrderSide.SELL))
        assert verdict.approved is True

    def test_sell_short_rejected(self):
        verdict = validate_order_intent(make_intent(side=OrderSide.SELL_SHORT))
        assert verdict.approved is False
        assert any("sell_short" in r.lower() or "short" in r.lower()
                   for r in verdict.blocked_reasons)

    def test_margin_account_rejected(self):
        verdict = validate_order_intent(
            make_intent(account_type=AccountType.MARGIN)
        )
        assert verdict.approved is False
        assert any("margin" in r.lower() for r in verdict.blocked_reasons)

    def test_derivative_blocked(self):
        intent = make_intent(symbol="AAPL_250117C200",
                             asset_class=AssetClass.DERIVATIVE)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("derivative" in r.lower() for r in verdict.blocked_reasons)

    def test_future_blocked(self):
        intent = make_intent(symbol="ES24",
                             asset_class=AssetClass.DERIVATIVE)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False

    def test_bond_blocked(self):
        intent = make_intent(symbol="UST10Y",
                             asset_class=AssetClass.BOND)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("bond" in r.lower() for r in verdict.blocked_reasons)

    def test_cfd_blocked(self):
        intent = make_intent(symbol="AAPL_CFD",
                             asset_class=AssetClass.CFD)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("cfd" in r.lower() for r in verdict.blocked_reasons)

    def test_spread_bet_blocked(self):
        intent = make_intent(symbol="AAPL_SPREAD",
                             asset_class=AssetClass.SPREAD_BET)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("spread" in r.lower() for r in verdict.blocked_reasons)

    def test_unknown_asset_class_blocked(self):
        intent = make_intent(asset_class=AssetClass.UNKNOWN)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False

    def test_equity_excluded_sector_blocked(self):
        intent = make_intent(symbol="BANK", sector="financial_services",
                             debt_to_market_cap=0.10)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("financial_services" in r.lower() for r in verdict.blocked_reasons)

    def test_equity_high_debt_blocked(self):
        intent = make_intent(sector="technology", debt_to_market_cap=0.80)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("exceeds" in r.lower() for r in verdict.blocked_reasons)

    def test_etf_not_impact_flagged_blocked(self):
        intent = make_intent(symbol="QQQ", asset_class=AssetClass.ETF,
                             is_impact_etf=False)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert any("whitelist" in r.lower() for r in verdict.blocked_reasons)

    def test_etf_impact_flagged_approved(self):
        intent = make_intent(symbol="SPUS", asset_class=AssetClass.ETF,
                             is_impact_etf=True)
        verdict = validate_order_intent(intent)
        assert verdict.approved is True

    def test_metal_not_spot_flagged_blocked(self):
        intent = make_intent(symbol="XAUUSD", asset_class=AssetClass.SPOT_METAL,
                             is_spot_metal=False)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False

    def test_metal_spot_flagged_approved(self):
        intent = make_intent(symbol="XAUUSD", asset_class=AssetClass.SPOT_METAL,
                             is_spot_metal=True)
        verdict = validate_order_intent(intent)
        assert verdict.approved is True

    def test_multiple_violations_all_reported(self):
        intent = make_intent(side=OrderSide.SELL_SHORT,
                             account_type=AccountType.MARGIN,
                             asset_class=AssetClass.BOND,
                             sector="tobacco",
                             debt_to_market_cap=0.90)
        verdict = validate_order_intent(intent)
        assert verdict.approved is False
        assert len(verdict.blocked_reasons) >= 4

    def test_verdict_contains_timestamp(self):
        verdict = validate_order_intent(make_intent())
        assert verdict.timestamp_utc is not None
        assert len(verdict.timestamp_utc) > 0


# ---------------------------------------------------------------------------
# engine.py tests
# ---------------------------------------------------------------------------


class TestImpactExecutionEngine:
    def test_approve_and_dry_run(self):
        engine = ImpactExecutionEngine(auto_submit=False)
        decision = engine.submit(make_intent(symbol="AAPL"))
        assert isinstance(decision, ExecutionDecision)
        assert decision.verdict.approved is True
        assert decision.submitted is False
        assert decision.order_id is None

    def test_auto_submit_assigns_order_id(self):
        engine = ImpactExecutionEngine(auto_submit=True)
        decision = engine.submit(make_intent(symbol="SPUS",
                                             asset_class=AssetClass.ETF,
                                             is_impact_etf=True))
        assert decision.verdict.approved is True
        assert decision.submitted is True
        assert decision.order_id is not None
        assert decision.order_id.startswith("SH-")

    def test_rejected_order_not_submitted(self):
        engine = ImpactExecutionEngine()
        decision = engine.submit(make_intent(side=OrderSide.SELL_SHORT))
        assert decision.verdict.approved is False
        assert decision.submitted is False
        assert decision.rejection_reason is not None

    def test_decisions_are_recorded_for_audit(self):
        engine = ImpactExecutionEngine()
        engine.submit(make_intent(symbol="AAPL"))
        engine.submit(make_intent(side=OrderSide.SELL_SHORT))
        assert len(engine.decisions()) == 2

    def test_record_interest_income(self):
        engine = ImpactExecutionEngine()
        record = engine.record_interest_income(
            source="IBKR cash balance",
            currency="USD",
            amount=12.50,
            account_id="U123456",
        )
        assert isinstance(record, InterestRecord)
        assert record.amount == 12.50
        assert record.redeemed is False

    def test_outstanding_redemption(self):
        engine = ImpactExecutionEngine()
        engine.record_interest_income("IBKR cash", "USD", 10.0, "U1")
        engine.record_interest_income("IBKR cash", "USD", 5.0, "U1")
        assert engine.outstanding_redemption("USD") == 15.0
        assert engine.outstanding_redemption("EUR") == 0.0

    def test_route_order_convenience(self):
        decision = route_order(make_intent(symbol="HLAL",
                                           asset_class=AssetClass.ETF,
                                           is_impact_etf=True),
                               auto_submit=True)
        assert decision.submitted is True


# ---------------------------------------------------------------------------
# redemption.py tests
# ---------------------------------------------------------------------------


class TestInterestRedemptionLedger:
    def test_record_interest(self):
        ledger = InterestRedemptionLedger()
        record = ledger.record_interest(
            source="IBKR cash balance",
            currency="USD",
            amount=100.0,
            account_id="U1",
        )
        assert record.amount == 100.0
        assert record.redeemed is False
        assert record.timestamp_utc is not None

    def test_negative_amount_raises(self):
        ledger = InterestRedemptionLedger()
        with pytest.raises(ValueError):
            ledger.record_interest("src", "USD", -1.0, "U1")

    def test_mark_redeemed(self):
        ledger = InterestRedemptionLedger()
        record = ledger.record_interest("src", "USD", 50.0, "U1")
        ledger.mark_redeemed(record, donation_reference="CHARITY-001")
        updated = ledger.records(redeemed=True)[0]
        assert updated.redeemed is True
        assert updated.donation_reference == "CHARITY-001"
        assert updated.redemption_timestamp_utc is not None

    def test_outstanding_excludes_redeemed(self):
        ledger = InterestRedemptionLedger()
        r1 = ledger.record_interest("src", "USD", 10.0, "U1")
        r2 = ledger.record_interest("src", "USD", 20.0, "U1")
        ledger.mark_redeemed(r1)
        assert ledger.outstanding_by_currency("USD") == 20.0
        assert ledger.total_outstanding() == 20.0
        assert ledger.total_redeemed() == 10.0

    def test_default_ledger_convenience(self):
        record = record_interest_income("IBKR cash", "USD", 7.5, "U1")
        assert record.amount == 7.5
        assert record.redeemed is False

    def test_records_filter_by_status(self):
        ledger = InterestRedemptionLedger()
        ledger.record_interest("src", "USD", 1.0, "U1")
        ledger.record_interest("src", "USD", 2.0, "U1")
        assert len(ledger.records(redeemed=False)) == 2
        assert len(ledger.records(redeemed=True)) == 0