"""Typed errors of the EG Austria API client (spec 01 "Errors").

Messages are always redacted before they reach an exception, so an exception
can be logged or shown in a repair without leaking tokens or ids.
"""

from __future__ import annotations


class EgAustriaError(Exception):
    """Base class for every error raised by this integration's client."""


class AuthError(EgAustriaError):
    """The token was rejected (401, or 403 with an auth-type body)."""


class TokenExpiredError(AuthError):
    """The stored token expiry has passed; no request was sent."""


class ForbiddenError(AuthError):
    """403 without an auth-type body (treated as ``AuthError`` in phase 1)."""


class ApiChangedError(EgAustriaError):
    """The portal API no longer behaves as specified (4xx, wrong content type)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        """Store the HTTP status (if any) next to the redacted message."""
        super().__init__(message)
        self.status = status


class StaleClientError(ApiChangedError):
    """400 whose body says the client is out of date ("aktualisiert", 1140/29)."""


class RateLimitedError(EgAustriaError):
    """429 Too Many Requests."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        """Store the parsed ``Retry-After`` seconds, if the portal sent them."""
        super().__init__(message)
        self.retry_after = retry_after


class PortalUnavailableError(EgAustriaError):
    """5xx, timeout, connection error, or a non-JSON body during maintenance."""

    def __init__(
        self, message: str, *, status: int | None = None, retryable: bool = False
    ) -> None:
        """Store the HTTP status (``None`` for timeouts and connection errors).

        ``retryable`` marks the cases the client retries once (timeout,
        connection error, 502/503/504).
        """
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class SchemaError(EgAustriaError):
    """A response does not match the models (spec 05 stage 1).

    ``path`` is the key path of the offending value, for example
    ``consumptionDataPoints[3].consumption``; ``rule`` is the spec 05 rule id
    (``S1`` wrong container, ``S2`` missing key, ``S3`` wrong type, ``S4`` bad
    timestamp, ``S9`` bad completeness date).
    """

    def __init__(self, rule: str, path: str, message: str) -> None:
        """Build ``"<rule> <path>: <message>"`` and keep the parts."""
        super().__init__(f"{rule} {path or '<root>'}: {message}")
        self.rule = rule
        self.path = path


class NotWhitelistedError(EgAustriaError):
    """A request outside the GET whitelist was attempted (ADR 0002)."""
