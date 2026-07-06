from __future__ import annotations


class PortfolioAutomationError(Exception):
    """Base exception for all portfolio automation errors."""


class ConfigError(PortfolioAutomationError):
    """Raised when configuration is invalid or missing."""


class BrokerError(PortfolioAutomationError):
    """Raised when a broker operation fails."""


class BrokerAuthenticationError(BrokerError):
    """Raised when broker authentication fails."""


class BrokerOrderRejectedError(BrokerError):
    """Raised when the broker rejects an order."""


class BrokerNetworkError(BrokerError):
    """Raised when a network error occurs during a broker operation."""


class BrokerSymbolNotFoundError(BrokerError):
    """Raised when a trading symbol is not found on the exchange."""


class OrderError(PortfolioAutomationError):
    """Raised when order placement or tracking fails."""


class SafetyViolationError(PortfolioAutomationError):
    """Raised when a safety check fails."""


class RuleEvaluationError(PortfolioAutomationError):
    """Raised when a rule condition cannot be evaluated."""


class RuleSecurityError(RuleEvaluationError):
    """Raised when a rule expression violates security sandboxing."""
