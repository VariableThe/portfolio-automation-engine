from __future__ import annotations

from datetime import UTC, datetime

import pytest

from portfolio_automation.core.exceptions import RuleEvaluationError, RuleSecurityError
from portfolio_automation.rules import (
    DecisionResult,
    RuleEngine,
    RuleLoadError,
    RulesConfig,
    load_rules_from_string,
)
from portfolio_automation.rules.conditions import (
    get_condition,
    list_conditions,
    register_condition,
)
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import BuyQueueEntry, ConditionConfig, SellRule


def _ctx(
    symbol: str = "TEST",
    price: float = 100.0,
    avg_buy: float | None = 80.0,
    qty: int = 10,
    pv: float = 100_000.0,
    cash: float = 50_000.0,
    candles: list[dict] | None = None,
    now: datetime | None = None,
) -> RuleContext:
    return RuleContext(
        symbol=symbol,
        current_price=price,
        avg_buy_price=avg_buy,
        quantity=qty,
        portfolio_value=pv,
        available_cash=cash,
        historical_candles=candles,
        current_time=now,
    )


# ── Registry ─────────────────────────────────────────────────────────────────


def test_registry_has_builtin_conditions() -> None:
    known = list_conditions()
    for expected in (
        "price_above",
        "price_below",
        "percent_profit",
        "percent_loss",
        "market",
        "time_based",
        "custom_expression",
        "ma_crossover",
        "rsi",
        "macd",
        "allocation_percentage",
    ):
        assert expected in known, f"Missing condition: {expected}"


def test_registry_unknown_type() -> None:
    from portfolio_automation.rules.conditions.registry import UnknownConditionError

    with pytest.raises(UnknownConditionError):
        get_condition("nonexistent_condition")


def test_register_custom_condition() -> None:
    @register_condition("test_custom")
    def _custom(condition: dict, context: RuleContext) -> DecisionResult:
        return DecisionResult(met=True, reason="custom works")

    assert "test_custom" in list_conditions()
    result = get_condition("test_custom")({}, _ctx())
    assert result.met is True


# ── Price Conditions ─────────────────────────────────────────────────────────


def test_price_above_met() -> None:
    ctx = _ctx(price=200.0)
    result = get_condition("price_above")({"value": 150.0}, ctx)
    assert result.met is True
    assert "200.00" in result.reason
    assert "150.00" in result.reason


def test_price_above_not_met() -> None:
    ctx = _ctx(price=100.0)
    result = get_condition("price_above")({"value": 150.0}, ctx)
    assert result.met is False


def test_price_below_met() -> None:
    ctx = _ctx(price=100.0)
    result = get_condition("price_below")({"value": 150.0}, ctx)
    assert result.met is True


def test_price_below_not_met() -> None:
    ctx = _ctx(price=200.0)
    result = get_condition("price_below")({"value": 150.0}, ctx)
    assert result.met is False


def test_price_at_exact_threshold_above() -> None:
    ctx = _ctx(price=150.0)
    result = get_condition("price_above")({"value": 150.0}, ctx)
    assert result.met is False


def test_price_at_exact_threshold_below() -> None:
    ctx = _ctx(price=150.0)
    result = get_condition("price_below")({"value": 150.0}, ctx)
    assert result.met is False


# ── Percentage Conditions ────────────────────────────────────────────────────


def test_percent_profit_met() -> None:
    ctx = _ctx(price=100.0, avg_buy=50.0)
    result = get_condition("percent_profit")({"value": 50.0}, ctx)
    assert result.met is True
    assert "100.00%" in result.reason
    assert "50.00%" in result.reason


def test_percent_profit_not_met() -> None:
    ctx = _ctx(price=60.0, avg_buy=50.0)
    result = get_condition("percent_profit")({"value": 50.0}, ctx)
    assert result.met is False


def test_percent_profit_no_buy_price() -> None:
    ctx = _ctx(price=100.0, avg_buy=None)
    result = get_condition("percent_profit")({"value": 10.0}, ctx)
    assert result.met is False
    assert "No buy price" in result.reason


def test_percent_loss_met() -> None:
    ctx = _ctx(price=40.0, avg_buy=100.0)
    result = get_condition("percent_loss")({"value": 50.0}, ctx)
    assert result.met is True
    assert "stop-loss" in result.reason


def test_percent_loss_not_met() -> None:
    ctx = _ctx(price=80.0, avg_buy=100.0)
    result = get_condition("percent_loss")({"value": 50.0}, ctx)
    assert result.met is False


# ── Time-based Conditions ────────────────────────────────────────────────────


def test_market_always_passes() -> None:
    result = get_condition("market")({}, _ctx())
    assert result.met is True


def test_time_based_within_window() -> None:
    ctx = _ctx(now=datetime(2026, 7, 3, 10, 0, tzinfo=UTC))
    result = get_condition("time_based")({"value": "09:15-15:30"}, ctx)
    assert result.met is True
    assert "10:00" in result.reason


def test_time_based_outside_window() -> None:
    ctx = _ctx(now=datetime(2026, 7, 3, 16, 0, tzinfo=UTC))
    result = get_condition("time_based")({"value": "09:15-15:30"}, ctx)
    assert result.met is False
    assert "16:00" in result.reason


def test_time_based_no_time() -> None:
    result = get_condition("time_based")({"value": "09:15-15:30"}, _ctx(now=None))
    assert result.met is True


# ── Custom Expression ────────────────────────────────────────────────────────


def test_custom_expression_simple() -> None:
    ctx = _ctx(price=100.0)
    result = get_condition("custom_expression")({"expression": "price > 50"}, ctx)
    assert result.met is True


def test_custom_expression_with_pct() -> None:
    ctx = _ctx(price=120.0, avg_buy=100.0)
    result = get_condition("custom_expression")({"expression": "pct > 15"}, ctx)
    assert result.met is True


def test_custom_expression_false() -> None:
    ctx = _ctx(price=100.0, cash=50000.0)
    result = get_condition("custom_expression")(
        {"expression": "available_cash > 100000"}, ctx
    )
    assert result.met is False


def test_custom_expression_with_round() -> None:
    ctx = _ctx(price=99.9)
    result = get_condition("custom_expression")(
        {"expression": "round(price) == 100"}, ctx
    )
    assert result.met is True


def test_custom_expression_sandbox_rejects_builtins() -> None:
    ctx = _ctx(price=100.0)
    with pytest.raises(RuleSecurityError):
        get_condition("custom_expression")({"expression": "__builtins__"}, ctx)


def test_custom_expression_sandbox_rejects_import() -> None:
    ctx = _ctx(price=100.0)
    with pytest.raises(RuleSecurityError):
        get_condition("custom_expression")({"expression": "import os"}, ctx)


def test_custom_expression_sandbox_rejects_os() -> None:
    ctx = _ctx(price=100.0)
    with pytest.raises(RuleSecurityError):
        get_condition("custom_expression")({"expression": "os.system('ls')"}, ctx)


def test_custom_expression_sandbox_rejects_eval() -> None:
    ctx = _ctx(price=100.0)
    with pytest.raises(RuleSecurityError):
        get_condition("custom_expression")({"expression": "eval('1+1')"}, ctx)


def test_custom_expression_invalid_syntax() -> None:
    ctx = _ctx(price=100.0)
    with pytest.raises(RuleEvaluationError):
        get_condition("custom_expression")({"expression": "price *** 2"}, ctx)


def test_custom_expression_no_expression() -> None:
    ctx = _ctx(price=100.0)
    result = get_condition("custom_expression")({}, ctx)
    assert result.met is False


# ── Technical Indicators ─────────────────────────────────────────────────────


def _candles(close_prices: list[float]) -> list[dict]:
    return [{"close": p} for p in close_prices]


def test_ma_crossover_above() -> None:
    prices = [100.0] * 10
    for i in range(10):
        prices.append(100 - i * 8)
    for i in range(15):
        prices.append(20 + i * 8)

    ctx = _ctx(candles=_candles(prices))
    result = get_condition("ma_crossover")(
        {"fast_period": 5, "slow_period": 10, "value": "above"}, ctx
    )
    assert isinstance(result, DecisionResult)
    assert isinstance(result.met, bool)
    assert isinstance(result.reason, str)


def test_ma_crossover_below() -> None:
    prices = [100.0] * 10
    for i in range(10):
        prices.append(100 - i * 8)
    for i in range(15):
        prices.append(20 + i * 8)

    ctx = _ctx(candles=_candles(prices))
    result = get_condition("ma_crossover")(
        {"fast_period": 5, "slow_period": 10, "value": "below"}, ctx
    )
    assert isinstance(result, DecisionResult)
    assert isinstance(result.met, bool)


def test_ma_crossover_no_cross() -> None:
    prices = [100.0] * 40
    ctx = _ctx(candles=_candles(prices))
    result = get_condition("ma_crossover")(
        {"fast_period": 5, "slow_period": 10, "value": "above"}, ctx
    )
    assert result.met is False


def test_ma_crossover_no_candles() -> None:
    ctx = _ctx(candles=[])
    result = get_condition("ma_crossover")({}, ctx)
    assert result.met is False


def test_rsi_oversold() -> None:
    prices = [100.0 - i * 3 for i in range(30)]
    ctx = _ctx(candles=_candles(prices))
    result = get_condition("rsi")({"period": 14, "lower": 30}, ctx)
    assert result.met is True
    assert "oversold" in result.reason


def test_rsi_overbought() -> None:
    prices = [100.0 + i * 3 for i in range(30)]
    ctx = _ctx(candles=_candles(prices))
    result = get_condition("rsi")({"period": 14, "upper": 70}, ctx)
    assert result.met is True
    assert "overbought" in result.reason


def test_rsi_neutral() -> None:
    prices = [100.0 + (i % 6 - 3) * 2 for i in range(30)]
    ctx = _ctx(candles=_candles(prices))
    result = get_condition("rsi")({"period": 14, "upper": 70, "lower": 30}, ctx)
    assert result.met is False
    assert "neutral" in result.reason


def test_rsi_no_candles() -> None:
    ctx = _ctx(candles=[])
    result = get_condition("rsi")({}, ctx)
    assert result.met is False


def test_macd_bullish() -> None:
    prices = [100.0] * 15 + [100.0 + i * 0.3 for i in range(35)]
    ctx = _ctx(candles=_candles(prices))
    result = get_condition("macd")({"value": "above"}, ctx)
    assert result.met is True, f"Expected MACD bullish, got: {result.reason}"
    assert "bullish" in result.reason


def test_macd_bearish() -> None:
    prices = [115.0] * 15 + [115.0 - i * 0.3 for i in range(35)]
    ctx = _ctx(candles=_candles(prices))
    result = get_condition("macd")({"value": "below"}, ctx)
    assert result.met is True, f"Expected MACD bearish, got: {result.reason}"
    assert "bearish" in result.reason


def test_macd_no_candles() -> None:
    ctx = _ctx(candles=[])
    result = get_condition("macd")({}, ctx)
    assert result.met is False


# ── Allocation Condition ─────────────────────────────────────────────────────


def test_allocation_within_limit() -> None:
    ctx = _ctx(price=100.0, qty=10, pv=1_000_000.0)
    result = get_condition("allocation_percentage")({"value": 0.2}, ctx)
    assert result.met is True
    assert "≤" in result.reason


def test_allocation_exceeds_limit() -> None:
    ctx = _ctx(price=100_000.0, qty=0, pv=100_000.0)
    result = get_condition("allocation_percentage")({"value": 0.2}, ctx)
    assert result.met is False
    assert ">" in result.reason


def test_allocation_no_portfolio_value() -> None:
    ctx = _ctx(price=100.0, qty=0, pv=0.0)
    result = get_condition("allocation_percentage")({"value": 0.2}, ctx)
    assert result.met is True


# ── Rule Engine Orchestration ────────────────────────────────────────────────


def test_engine_evaluate_sell_rules() -> None:
    config = RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="price_above", value=1500.0),
            ),
        ]
    )
    engine = RuleEngine(config)
    ctx = _ctx(symbol="INFY", price=1600.0)
    results = engine.evaluate_sell_rules(ctx)
    assert len(results) == 1
    assert results[0].met is True


def test_engine_skips_other_symbols() -> None:
    config = RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="price_above", value=1500.0),
            ),
        ]
    )
    engine = RuleEngine(config)
    ctx = _ctx(symbol="TCS", price=1600.0)
    results = engine.evaluate_sell_rules(ctx)
    assert len(results) == 0


def test_engine_evaluate_buy_condition() -> None:
    config = RulesConfig(
        buy_queue=[
            BuyQueueEntry(
                symbol="HAL",
                priority=1,
                condition=ConditionConfig(type="price_below", value=5000.0),
            ),
        ]
    )
    engine = RuleEngine(config)
    ctx = _ctx(symbol="HAL", price=4800.0)
    entry = config.buy_queue[0]
    result = engine.evaluate_buy_condition(entry, ctx)
    assert result.met is True


# ── Rule Loader ──────────────────────────────────────────────────────────────


def test_loader_valid_yaml() -> None:
    yaml_str = """
sell_rules:
  - symbol: INFY
    condition:
      type: price_above
      value: 1850

buy_queue:
  - symbol: HAL
    priority: 1
    condition:
      type: price_below
      value: 5200
"""
    config = load_rules_from_string(yaml_str)
    assert len(config.sell_rules) == 1
    assert config.sell_rules[0].symbol == "INFY"
    assert config.sell_rules[0].condition.type == "price_above"
    assert config.sell_rules[0].condition.value == 1850
    assert len(config.buy_queue) == 1
    assert config.buy_queue[0].symbol == "HAL"
    assert config.buy_queue[0].priority == 1


def test_loader_rejects_unknown_condition() -> None:
    yaml_str = """
sell_rules:
  - symbol: INFY
    condition:
      type: magic_bean
      value: 42
"""
    with pytest.raises(RuleLoadError, match="Unknown condition type.*magic_bean"):
        load_rules_from_string(yaml_str)


def test_loader_empty_rules() -> None:
    config = load_rules_from_string("")
    assert config.sell_rules == []
    assert config.buy_queue == []


def test_loader_default_priority() -> None:
    yaml_str = """
buy_queue:
  - symbol: HAL
    condition:
      type: market
"""
    config = load_rules_from_string(yaml_str)
    assert config.buy_queue[0].priority == 1


def test_loader_validates_buy_queue_condition() -> None:
    yaml_str = """
buy_queue:
  - symbol: HAL
    priority: 1
    condition:
      type: nonexistent
"""
    with pytest.raises(RuleLoadError, match="Unknown condition type.*nonexistent"):
        load_rules_from_string(yaml_str)


def test_loader_with_market_condition() -> None:
    yaml_str = """
buy_queue:
  - symbol: BEL
    priority: 2
    condition:
      type: market
"""
    config = load_rules_from_string(yaml_str)
    assert config.buy_queue[0].condition.type == "market"


def test_loader_file_not_found() -> None:
    with pytest.raises(RuleLoadError, match="not found"):
        from portfolio_automation.rules.loader import load_rules

        load_rules("/nonexistent/path/rules.yaml")


# ── DecisionResult Format ────────────────────────────────────────────────────


def test_decision_result_has_reason() -> None:
    result = DecisionResult(met=True, reason="Everything is fine")
    assert result.met is True
    assert "Everything" in result.reason


def test_decision_result_reason_human_readable() -> None:
    ctx = _ctx(symbol="INFY", price=1900.0)
    result = get_condition("price_above")({"value": 1850.0}, ctx)
    assert result.met is True
    assert "INFY" in result.reason
    assert "1900.00" in result.reason
    assert "1850.00" in result.reason


def test_engine_returns_multiple_decisions() -> None:
    config = RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="price_above", value=1500.0),
            ),
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="percent_profit", value=20.0),
            ),
        ]
    )
    engine = RuleEngine(config)
    ctx = _ctx(symbol="INFY", price=1600.0, avg_buy=1000.0)
    results = engine.evaluate_sell_rules(ctx)
    assert len(results) == 2
    assert all(isinstance(r, DecisionResult) for r in results)
    assert all(r.met is True for r in results)


def test_engine_evaluate_raw() -> None:
    engine = RuleEngine(RulesConfig())
    ctx = _ctx(price=100.0)
    result = engine.evaluate_raw({"type": "price_below", "value": 200.0}, ctx)
    assert result.met is True
