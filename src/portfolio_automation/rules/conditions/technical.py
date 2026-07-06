from __future__ import annotations

from typing import Any

from portfolio_automation.rules.conditions.registry import register_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult


def _extract_prices(
    candles: list[dict[str, Any]],
    key: str = "close",
) -> list[float]:
    result: list[float] = []
    for c in candles:
        val = c.get(key)
        if isinstance(val, (int, float)):
            result.append(float(val))
    return result


def _sma(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    return [
        sum(prices[i - period : i]) / period for i in range(period, len(prices) + 1)
    ]


def _ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return []
    multiplier = 2.0 / (period + 1)
    result: list[float] = []
    result.append(sum(prices[:period]) / period)
    for p in prices[period:]:
        result.append((p - result[-1]) * multiplier + result[-1])
    return result


def _rsi_from_prices(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd_from_prices(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float]:
    ema_fast = _ema(prices, fast)
    ema_slow = _ema(prices, slow)
    align = len(ema_slow)
    macd_line = ema_fast[-align:] if len(ema_fast) >= align else ema_fast
    macd_line = [m - s for m, s in zip(macd_line, ema_slow)]
    if len(macd_line) < signal_period:
        return macd_line[-1] if macd_line else 0.0, 0.0, 0.0
    signal = _ema(macd_line, signal_period)
    sig_val = signal[-1] if signal else 0.0
    macd_val = macd_line[-1] if macd_line else 0.0
    hist_val = macd_val - sig_val
    return macd_val, sig_val, hist_val


@register_condition("ma_crossover")
def evaluate_ma_crossover(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    candles = context.historical_candles
    if not candles:
        return DecisionResult(met=False, reason="No historical candles available")

    prices = _extract_prices(candles)
    fast = int(condition.get("fast_period", condition.get("period", 10)))
    slow = int(condition.get("slow_period", 30))

    fast_sma = _sma(prices, fast)
    slow_sma = _sma(prices, slow)
    if len(fast_sma) < 2 or len(slow_sma) < 2:
        return DecisionResult(
            met=False,
            reason=f"Not enough data for MA crossover ({fast}/{slow})",
        )

    prev_fast = fast_sma[-2]
    curr_fast = fast_sma[-1]
    prev_slow = slow_sma[-2]
    curr_slow = slow_sma[-1]

    crossed_above = prev_fast <= prev_slow and curr_fast > curr_slow
    crossed_below = prev_fast >= prev_slow and curr_fast < curr_slow

    direction = condition.get("value", "above")
    if direction == "above" and crossed_above:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} MA{fast} ({curr_fast:.2f}) "
                f"crossed above MA{slow} ({curr_slow:.2f})"
            ),
        )
    if direction == "below" and crossed_below:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} MA{fast} ({curr_fast:.2f}) "
                f"crossed below MA{slow} ({curr_slow:.2f})"
            ),
        )
    return DecisionResult(
        met=False,
        reason=(
            f"{context.symbol} MA{fast}={curr_fast:.2f}, "
            f"MA{slow}={curr_slow:.2f} — no crossover"
        ),
    )


@register_condition("rsi")
def evaluate_rsi(condition: dict[str, Any], context: RuleContext) -> DecisionResult:
    candles = context.historical_candles
    if not candles:
        return DecisionResult(met=False, reason="No historical candles available")

    prices = _extract_prices(candles)
    period = int(condition.get("period", 14))
    upper = float(condition.get("upper", 70))
    lower = float(condition.get("lower", 30))
    value = condition.get("value")

    rsi_val = _rsi_from_prices(prices, period)

    if value is not None:
        val = float(value)
        if rsi_val > val:
            return DecisionResult(
                met=True,
                reason=f"{context.symbol} RSI({period})={rsi_val:.1f} > {val}",
            )
        return DecisionResult(
            met=False,
            reason=f"{context.symbol} RSI({period})={rsi_val:.1f} ≤ {val}",
        )

    if rsi_val >= upper:
        return DecisionResult(
            met=True,
            reason=f"{context.symbol} RSI({period})={rsi_val:.1f} ≥ overbought {upper}",
        )
    if rsi_val <= lower:
        return DecisionResult(
            met=True,
            reason=f"{context.symbol} RSI({period})={rsi_val:.1f} ≤ oversold {lower}",
        )
    return DecisionResult(
        met=False,
        reason=f"{context.symbol} RSI({period})={rsi_val:.1f} in neutral range ({lower}-{upper})",
    )


@register_condition("macd")
def evaluate_macd(condition: dict[str, Any], context: RuleContext) -> DecisionResult:
    candles = context.historical_candles
    if not candles:
        return DecisionResult(met=False, reason="No historical candles available")

    prices = _extract_prices(candles)
    fast = int(condition.get("fast_period", 12))
    slow = int(condition.get("slow_period", 26))
    sig = int(condition.get("signal_period", 9))

    macd_val, signal_val, hist_val = _macd_from_prices(prices, fast, slow, sig)

    direction = condition.get("value", "above")
    if direction == "above" and macd_val > signal_val:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} MACD ({macd_val:.2f}) "
                f"> signal ({signal_val:.2f}) — bullish crossover"
            ),
        )
    if direction == "below" and macd_val < signal_val:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} MACD ({macd_val:.2f}) "
                f"< signal ({signal_val:.2f}) — bearish crossover"
            ),
        )
    if direction == "histogram_positive" and hist_val > 0:
        return DecisionResult(
            met=True,
            reason=(f"{context.symbol} MACD histogram ({hist_val:.2f}) positive"),
        )
    if direction == "histogram_negative" and hist_val < 0:
        return DecisionResult(
            met=True,
            reason=(f"{context.symbol} MACD histogram ({hist_val:.2f}) negative"),
        )
    return DecisionResult(
        met=False,
        reason=(
            f"{context.symbol} MACD={macd_val:.2f}, "
            f"signal={signal_val:.2f}, hist={hist_val:.2f}"
        ),
    )
