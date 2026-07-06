from __future__ import annotations

from typing import Any

from portfolio_automation.notifications.interface import EventType


def format_message(event_type: str, data: dict[str, Any]) -> str:
    """Generate human-readable notification message for an event type."""
    symbol = data.get("symbol", "N/A")
    side = str(data.get("side", "")).upper()
    quantity = data.get("quantity")
    price = (
        data.get("price") if data.get("price") is not None else data.get("filled_price")
    )
    total = data.get("total")
    if total is None and quantity is not None and price is not None:
        try:
            total = float(quantity) * float(price)
        except (ValueError, TypeError):
            total = None
    order_id = data.get("order_id", data.get("id", "N/A"))
    reason = data.get("reason", data.get("error_message", "Unknown reason"))
    is_dry_run = data.get("is_dry_run", False)
    dry_prefix = "(DRY RUN) " if is_dry_run else ""

    if event_type == EventType.ORDER_FILLED:
        emoji = "🟢" if side != "SELL" else "🔴"
        side_str = f"{side} " if side else ""
        lines = [f"{emoji} {dry_prefix}{side_str}ORDER FILLED"]
        lines.append(f"Symbol: {symbol}")
        if quantity is not None:
            lines.append(f"Quantity: {quantity}")
        if price is not None:
            lines.append(f"Price: ₹{float(price):,.2f}")
        if total is not None:
            lines.append(f"Total: ₹{float(total):,.2f}")
        if order_id != "N/A":
            lines.append(f"Order ID: {order_id}")
        return "\n".join(lines)

    if event_type == EventType.ORDER_PLACED:
        emoji = "🟢" if side == "BUY" else ("🔴" if side == "SELL" else "📋")
        side_str = f"{side} " if side else ""
        lines = [f"{emoji} {dry_prefix}{side_str}ORDER PLACED"]
        lines.append(f"Symbol: {symbol}")
        if quantity is not None:
            lines.append(f"Quantity: {quantity}")
        if price is not None:
            lines.append(f"Price: ₹{float(price):,.2f}")
        if total is not None:
            lines.append(f"Total: ₹{float(total):,.2f}")
        if order_id != "N/A":
            lines.append(f"Order ID: {order_id}")
        return "\n".join(lines)

    if event_type == EventType.ORDER_REJECTED:
        side_str = f"{side} " if side else ""
        lines = [f"⚠️ {dry_prefix}{side_str}ORDER REJECTED"]
        lines.append(f"Symbol: {symbol}")
        lines.append(f"Reason: {reason}")
        if order_id != "N/A":
            lines.append(f"Order ID: {order_id}")
        return "\n".join(lines)

    if event_type == EventType.SAFETY_TRIGGERED:
        lines = ["🛡️ SAFETY LIMIT TRIGGERED"]
        if symbol != "N/A":
            lines.append(f"Symbol: {symbol}")
        if side:
            lines.append(f"Side: {side}")
        lines.append(f"Reason: {reason}")
        return "\n".join(lines)

    if event_type == EventType.ERROR:
        msg = data.get("message", data.get("error", "System Error"))
        lines = ["❌ SYSTEM ERROR"]
        lines.append(f"Message: {msg}")
        if "reason" in data and data["reason"] != msg:
            lines.append(f"Details: {data['reason']}")
        if symbol != "N/A":
            lines.append(f"Symbol: {symbol}")
        return "\n".join(lines)

    if event_type == EventType.DAILY_SUMMARY:
        lines = ["📊 DAILY PORTFOLIO SUMMARY"]
        if "total_value" in data:
            lines.append(f"Total Portfolio Value: ₹{float(data['total_value']):,.2f}")
        if "available_cash" in data:
            lines.append(f"Available Cash: ₹{float(data['available_cash']):,.2f}")
        if "total_trades" in data:
            lines.append(f"Total Trades Today: {data['total_trades']}")
        if "holdings_count" in data:
            lines.append(f"Active Holdings: {data['holdings_count']}")
        return "\n".join(lines)

    lines = [f"📢 {event_type}"]
    for k, v in data.items():
        lines.append(f"{k.capitalize()}: {v}")
    return "\n".join(lines)
