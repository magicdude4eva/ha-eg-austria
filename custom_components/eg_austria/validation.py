"""Response validation (spec 05, ADR 0005 and 0006).

Pure functions over the parsed models of ``models.py``: no Home Assistant
imports, no I/O, no logging of values. Every check returns a
``ValidationResult``; the coordinator (S5) decides what to write and turns the
findings into repairs and diagnostics (S6).

Stages:

* **Shape** (S1-S9). S1-S4 and the date part of S9 are raised by the parser
  as ``SchemaError``; ``schema_failure()`` turns that into a result. S5-S8 run
  in ``validate_chunk()``, S9 in ``validate_completeness()``. Any shape failure
  means the whole run writes nothing (``ValidationResult.schema_failed``).
* **Plausibility** (P1-P6) in ``validate_chunk()``. A failure blocks the
  statistics involved for that chunk only (``is_blocked()``).
* **History guard** (H1-H2) in ``check_history()``, against the stored day
  totals the caller reads from the recorder.

Statistic keys follow spec 04: ``<site>_<quantity>`` and
``<site>_<community>_<quantity>``. Callers pass the per-community responses
keyed by community slug and the site slug, so keys match the statistic ids.

Messages name rules, fields, statistic keys and UTC hours only: never energy
values, member numbers or portal ids.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
import math
from typing import Final
from zoneinfo import ZoneInfo

from .exceptions import SchemaError
from .models import (
    Aggregation,
    AnalysisData,
    DayCompleteness,
    Direction,
    EnergyPoint,
)
from .redact import redact_text

TZ: Final = ZoneInfo("Europe/Vienna")

# Option default (spec 02 ``max_hourly_kwh``).
DEFAULT_MAX_HOURLY_KWH: Final = 50.0

# Tolerances (spec 05 stage 2): a check passes when the difference is within
# the absolute or the relative tolerance.
P3_TOLERANCE_KWH: Final = 0.001
P4_TOLERANCE_KWH: Final = 0.002
P4_TOLERANCE_REL: Final = 0.005
P5_TOLERANCE_KWH: Final = 0.01
P5_TOLERANCE_REL: Final = 0.001
P6_TOLERANCE_KWH: Final = 0.002

# H2 thresholds: a day total change above both is recorded as a correction.
H2_MIN_RELATIVE: Final = 0.5
H2_MIN_KWH: Final = 1.0

# Whether a rule blocks writes (True) or is only reported as a warning. Spec
# 05: P1-P6 block; flip one to False if a portal change breaks an identity.
RULE_BLOCKING: Final[Mapping[str, bool]] = {
    "P1": True,
    "P2": True,
    "P3": True,
    "P4": True,
    "P5": True,
    "P6": True,
}

SHAPE_RULES: Final = frozenset(f"S{i}" for i in range(1, 10))

# Point field -> quantity key (spec 04 "Mapping").
_FIELDS: Final = ("value", "community", "excess")
_QUANTITY: Final[Mapping[tuple[Direction, str], str]] = {
    (Direction.CONSUMPTION, "value"): "consumption",
    (Direction.CONSUMPTION, "community"): "consumption_from_community",
    (Direction.CONSUMPTION, "excess"): "consumption_from_supplier",
    (Direction.GENERATION, "value"): "generation",
    (Direction.GENERATION, "community"): "generation_to_community",
    (Direction.GENERATION, "excess"): "generation_to_surplus",
}
_TOTAL_ATTR: Final[Mapping[tuple[Direction, str], str]] = {
    (Direction.CONSUMPTION, "value"): "consumption",
    (Direction.CONSUMPTION, "community"): "consumption_from_community",
    (Direction.CONSUMPTION, "excess"): "consumption_in_excess",
    (Direction.GENERATION, "value"): "generation",
    (Direction.GENERATION, "community"): "generation_to_community",
    (Direction.GENERATION, "excess"): "generation_in_excess",
}
_LIST_NAME: Final = {
    Direction.CONSUMPTION: "consumptionDataPoints",
    Direction.GENERATION: "generationDataPoints",
}


class Severity(StrEnum):
    """How a finding affects writes."""

    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule result.

    ``statistic`` is ``None`` for shape findings (they stop the whole run).
    ``chunk`` is the requested local date range (both ends inclusive);
    ``day`` is set for history findings.
    """

    rule: str
    severity: Severity
    message: str
    statistic: str | None = None
    chunk: tuple[date, date] | None = None
    day: date | None = None

    @property
    def is_shape(self) -> bool:
        """Return True for a stage 1 rule (S1-S9)."""

        return self.rule in SHAPE_RULES


@dataclass(frozen=True, slots=True)
class Correction:
    """A large day-total change (H2), recorded for diagnostics only."""

    day: date
    statistic: str
    relative_change: float | None  # None when the stored total was 0


@dataclass(slots=True)
class ValidationResult:
    """Outcome of one or more checks; merge per run with ``merge()``."""

    findings: list[Finding] = field(default_factory=list)
    schema_extra: set[str] = field(default_factory=set)
    held_days: set[tuple[str, date]] = field(default_factory=set)
    corrections: list[Correction] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        """Blocking findings."""

        return [f for f in self.findings if f.severity is Severity.BLOCKING]

    @property
    def warnings(self) -> list[Finding]:
        """Non-blocking findings."""

        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """Return True when nothing blocks (warnings may exist)."""

        return not self.failures

    @property
    def schema_failed(self) -> bool:
        """Return True when a shape rule failed: the run writes nothing."""

        return any(f.is_shape for f in self.failures)

    def rule_ids(self) -> set[str]:
        """Rule ids of every finding."""

        return {f.rule for f in self.findings}

    def blocked_statistics(self) -> set[str]:
        """Statistic keys with at least one blocking finding."""

        return {f.statistic for f in self.failures if f.statistic is not None}

    def is_blocked(self, statistic: str, day: date) -> bool:
        """Return True if ``statistic`` must not be written for local ``day``."""

        if self.schema_failed or (statistic, day) in self.held_days:
            return True
        for f in self.failures:
            if f.statistic != statistic:
                continue
            if f.day == day or (
                f.chunk is not None and f.chunk[0] <= day <= f.chunk[1]
            ):
                return True
        return False

    def held_by_statistic(self) -> dict[str, list[date]]:
        """``held_days`` in the store layout of spec 03."""

        out: dict[str, list[date]] = defaultdict(list)
        for statistic, day in sorted(self.held_days):
            out[statistic].append(day)
        return dict(out)

    def merge(self, *others: ValidationResult) -> ValidationResult:
        """Add the other results into this one and return it."""

        for other in others:
            self.findings.extend(other.findings)
            self.schema_extra |= other.schema_extra
            self.held_days |= other.held_days
            self.corrections.extend(other.corrections)
        return self


# ---------------------------------------------------------------------------
# Helpers


def local_midnight(day: date) -> datetime:
    """Return local 00:00 of ``day`` in UTC."""

    return datetime.combine(day, time(), TZ).astimezone(UTC)


def local_day(moment: datetime) -> date:
    """Return the Austrian local date of a UTC moment."""

    return moment.astimezone(TZ).date()


def hours_in_day(day: date) -> int:
    """Return 23, 24 or 25: the local hours of ``day`` (DST aware)."""

    span = local_midnight(day + timedelta(days=1)) - local_midnight(day)
    return int(span.total_seconds() // 3600)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _hour(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%MZ")


def _close(a: float, b: float, abs_tol: float, rel_tol: float = 0.0) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


def _key(site: str, quantity: str, community: str | None = None) -> str:
    parts = [p for p in (site, community, quantity) if p]
    return "_".join(parts)


def _statistics(
    site: str, direction: Direction, fld: str, community: str | None
) -> list[str]:
    """Statistic keys fed by one field of one community's response (spec 04)."""

    quantity = _QUANTITY[direction, fld]
    keys = [_key(site, quantity)]
    if fld == "community" and community is not None:
        keys.append(_key(site, quantity, community))
    return keys


def _severity(rule: str, blocking: Mapping[str, bool] | None) -> Severity:
    table = RULE_BLOCKING if blocking is None else {**RULE_BLOCKING, **blocking}
    return Severity.BLOCKING if table.get(rule, True) else Severity.WARNING


def _scope(chunk: tuple[date, date]) -> str:
    return f"{chunk[0].isoformat()}..{chunk[1].isoformat()}"


class _Collector:
    """Groups hits per (rule, statistic) into one finding with a count."""

    def __init__(
        self,
        chunk: tuple[date, date],
        blocking: Mapping[str, bool] | None,
        sensitive: Iterable[object],
    ) -> None:
        self.chunk = chunk
        self.blocking = blocking
        self.sensitive = tuple(sensitive)
        self._hits: dict[tuple[str, str | None], list[str]] = {}
        self._order: list[tuple[str, str | None, str]] = []

    def hit(self, rule: str, statistic: str | None, text: str, where: str) -> None:
        key = (rule, statistic)
        if key not in self._hits:
            self._hits[key] = []
            self._order.append((rule, statistic, text))
        self._hits[key].append(where)

    def findings(self) -> list[Finding]:
        out = []
        for rule, statistic, text in self._order:
            where = self._hits[rule, statistic]
            count = f" ({len(where)} times)" if len(where) > 1 else ""
            message = f"{rule} {_scope(self.chunk)}: {text} at {where[0]}{count}"
            out.append(
                Finding(
                    rule=rule,
                    severity=(
                        Severity.BLOCKING
                        if rule in SHAPE_RULES
                        else _severity(rule, self.blocking)
                    ),
                    message=redact_text(message, self.sensitive),
                    statistic=statistic,
                    chunk=self.chunk,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Stage 1


def schema_failure(
    err: SchemaError,
    *,
    chunk: tuple[date, date] | None = None,
    sensitive: Iterable[object] = (),
) -> ValidationResult:
    """Turn a parser ``SchemaError`` (S1-S4, S9) into a result."""

    rule = err.rule if err.rule in SHAPE_RULES else "S2"
    return ValidationResult(
        findings=[
            Finding(
                rule=rule,
                severity=Severity.BLOCKING,
                message=redact_text(str(err), sensitive),
                chunk=chunk,
            )
        ]
    )


def _check_shape(
    data: AnalysisData,
    chunk: tuple[date, date],
    aggregation: Aggregation,
    label: str,
    out: _Collector,
) -> None:
    start, end = chunk
    lower = local_midnight(start)
    upper = local_midnight(end + timedelta(days=1))
    if aggregation is Aggregation.DAY:
        limit = len(_days(start, end))
    else:
        limit = sum(hours_in_day(d) for d in _days(start, end))

    for direction in Direction:
        points = data.points(direction)
        name = f"{label}{_LIST_NAME[direction]}"
        seen: set[datetime] = set()
        for p in points:
            where = _hour(p.start)
            if aggregation is Aggregation.HOUR and (
                p.start.minute or p.start.second or p.start.microsecond
            ):
                out.hit("S5", None, f"{name}: timestamp not on a whole hour", where)
            if aggregation is Aggregation.DAY and p.start != local_midnight(
                local_day(p.start)
            ):
                out.hit("S5", None, f"{name}: Day point not at local midnight", where)
            if p.start in seen:
                out.hit("S6", None, f"{name}: duplicate timestamp", where)
            seen.add(p.start)
            if not lower <= p.start < upper:
                out.hit("S7", None, f"{name}: timestamp outside the range", where)
        if len(points) > limit:
            out.hit(
                "S8",
                None,
                f"{name}: {len(points)} points for {limit} {aggregation.lower()}s",
                _scope(chunk),
            )


def validate_completeness(
    entries: Iterable[DayCompleteness],
    start: date,
    end: date,
    *,
    sensitive: Iterable[object] = (),
) -> ValidationResult:
    """S9 on a parsed ``day-completeness`` list (dates unique and in range).

    A missing day is not a failure: it counts as not complete (spec 01).
    """

    out = _Collector((start, end), None, sensitive)
    result = ValidationResult()
    seen: set[date] = set()
    for entry in entries:
        where = entry.day.isoformat()
        if entry.day in seen:
            out.hit("S9", None, "day-completeness: duplicate date", where)
        seen.add(entry.day)
        if not start <= entry.day <= end:
            out.hit("S9", None, "day-completeness: date outside the range", where)
        result.schema_extra.update(f"day-completeness[].{k}" for k in entry.extra)
    result.findings = out.findings()
    return result


# ---------------------------------------------------------------------------
# Stage 2


def _by_hour(data: AnalysisData, direction: Direction) -> dict[datetime, EnergyPoint]:
    return {p.start: p for p in data.points(direction)}


def _check_values(
    data: AnalysisData,
    slug: str,
    site: str,
    limit: float,
    out: _Collector,
) -> None:
    """P1 (no negative), P2 (upper bound), P3 (community <= meter)."""

    for direction in Direction:
        for p in data.points(direction):
            where = _hour(p.start)
            for fld in _FIELDS:
                v = getattr(p, fld)
                if v is None:
                    continue
                for stat in _statistics(site, direction, fld, slug):
                    if v < 0:
                        out.hit("P1", stat, f"{slug}: negative value", where)
                    if v > limit:
                        out.hit("P2", stat, f"{slug}: value above the limit", where)
            if (
                p.community is not None
                and p.value is not None
                and p.community > p.value + P3_TOLERANCE_KWH
            ):
                for fld in ("community", "value"):
                    for stat in _statistics(site, direction, fld, slug):
                        out.hit(
                            "P3", stat, f"{slug}: community value above meter", where
                        )


def _check_totals(data: AnalysisData, slug: str, site: str, out: _Collector) -> None:
    """P5: point sums equal the response's ``total*``."""

    for direction in Direction:
        points = data.points(direction)
        for fld in _FIELDS:
            total = getattr(data.totals, _TOTAL_ATTR[direction, fld])
            if total is None:
                continue
            values = [getattr(p, fld) for p in points]
            point_sum = math.fsum(v for v in values if v is not None)
            if not _close(point_sum, total, P5_TOLERANCE_KWH, P5_TOLERANCE_REL):
                for stat in _statistics(site, direction, fld, slug):
                    out.hit(
                        "P5",
                        stat,
                        f"{slug}: point sum differs from total "
                        f"{_TOTAL_ATTR[direction, fld]}",
                        _scope(out.chunk),
                    )


def _check_cross(
    responses: Mapping[str, AnalysisData],
    site: str,
    all_communities: bool,
    out: _Collector,
) -> None:
    """P4 (community split adds up) and P6 (meter/residual equal everywhere).

    The residual is global (G1), so the P4 equality needs every community the
    metering points are active in. With a subset, P4 only checks that the
    parts and the residual do not exceed the meter.
    """

    slugs = list(responses)
    for direction in Direction:
        hours = [_by_hour(responses[s], direction) for s in slugs]
        common = set(hours[0]).intersection(*hours[1:]) if hours else set()
        for moment in sorted(common):
            where = _hour(moment)
            points = [h[moment] for h in hours]
            first = points[0]

            # P6: meter and residual identical in every per-community response.
            # A meter value flagged faulty is unknown (ADR 0005), not a value
            # that differs: one community may report a faulty 0 while another
            # has the reading (issue #78).
            for fld in ("value", "excess"):
                values = [
                    getattr(p, fld) for p in points if not (fld == "value" and p.faulty)
                ]
                known = [v for v in values if v is not None]
                if len(known) != len(values) and known:
                    differs = True
                else:
                    differs = any(
                        not _close(v, known[0], P6_TOLERANCE_KWH) for v in known
                    )
                if differs:
                    for stat in _statistics(site, direction, fld, None):
                        out.hit("P6", stat, f"{fld} differs between communities", where)

            # P4: sum of community parts + residual = meter. The meter comes
            # from the first response whose meter value isn't faulty, as for
            # the site statistic (issue #78); all faulty: nothing to check.
            parts = [p.community for p in points]
            meter = next((p for p in points if not p.faulty), None)
            if (
                meter is None
                or meter.value is None
                or first.excess is None
                or None in parts
            ):
                continue
            expected = meter.value
            actual = math.fsum(v for v in parts if v is not None) + first.excess
            if all_communities:
                bad = not _close(actual, expected, P4_TOLERANCE_KWH, P4_TOLERANCE_REL)
            else:
                bad = actual > expected + max(
                    P4_TOLERANCE_KWH, P4_TOLERANCE_REL * abs(expected)
                )
            if bad:
                stats = _statistics(site, direction, "value", None) + _statistics(
                    site, direction, "excess", None
                )
                stats += [
                    k
                    for s in slugs
                    for k in _statistics(site, direction, "community", s)
                    if k not in stats
                ]
                for stat in stats:
                    out.hit(
                        "P4",
                        stat,
                        "community parts and residual do not add up to the meter",
                        where,
                    )


def validate_chunk(
    start: date,
    end: date,
    responses: Mapping[str, AnalysisData],
    *,
    site: str = "",
    aggregation: Aggregation = Aggregation.HOUR,
    all_communities: bool = True,
    max_hourly_kwh: float = DEFAULT_MAX_HOURLY_KWH,
    blocking: Mapping[str, bool] | None = None,
    sensitive: Iterable[object] = (),
) -> ValidationResult:
    """Validate one chunk: the per-community responses for ``start..end``.

    ``responses`` maps community slug -> parsed ``analysis-data`` for the same
    range and aggregation (one request per community, spec 03). Runs S5-S8 on
    every response; if they pass, P1-P6. ``blocking`` overrides
    ``RULE_BLOCKING`` per rule (``{"P4": False}`` downgrades P4 to a warning).
    ``all_communities`` is False when the entry selects only some of the
    communities the metering points are active in (P4 is then one-sided).
    For ``Day`` the P2 limit is ``max_hourly_kwh`` times the day's hours.
    ``sensitive`` holds ids to redact from messages (member, metering points).
    """

    chunk = (start, end)
    result = ValidationResult()
    shape = _Collector(chunk, blocking, sensitive)
    for slug, data in responses.items():
        result.schema_extra |= data.extra_keys()
        _check_shape(data, chunk, aggregation, f"{slug}: ", shape)
    result.findings = shape.findings()
    if result.findings:
        return result

    plaus = _Collector(chunk, blocking, sensitive)
    limit = max_hourly_kwh
    if aggregation is Aggregation.DAY:
        limit = max_hourly_kwh * max(hours_in_day(d) for d in _days(start, end))
    for slug, data in responses.items():
        _check_values(data, slug, site, limit, plaus)
        _check_totals(data, slug, site, plaus)
    if responses:
        _check_cross(responses, site, all_communities, plaus)
    result.findings = plaus.findings()
    return result


# ---------------------------------------------------------------------------
# Stage 3


def _completeness_map(
    entries: Iterable[DayCompleteness],
) -> dict[date, DayCompleteness]:
    return {e.day: e for e in entries}


def day_totals(
    responses: Mapping[str, AnalysisData],
    completeness: Iterable[DayCompleteness],
    *,
    site: str = "",
) -> dict[tuple[str, date], tuple[float, bool]]:
    """Per (statistic, local day) on days complete for the statistic's side.

    Returns ``(total, all_zero)``: the sum of the known hourly values and
    whether every known hour is ``0``. Days that are not complete are left
    out: their values are provisional and spec 04 treats them as unknown in
    part. Meter and residual values come from the first response (P6).
    """

    complete = _completeness_map(completeness)
    sums: dict[tuple[str, date], list[float]] = defaultdict(list)
    for index, (slug, data) in enumerate(responses.items()):
        for direction in Direction:
            for p in data.points(direction):
                day = local_day(p.start)
                entry = complete.get(day)
                if entry is None or not entry.complete(direction):
                    continue
                for fld in _FIELDS:
                    v = getattr(p, fld)
                    if v is None:
                        continue
                    if fld != "community" and index:
                        continue  # site meter/residual from the first response
                    for stat in _statistics(site, direction, fld, slug):
                        sums[stat, day].append(v)
    return {k: (math.fsum(v), all(x == 0 for x in v)) for k, v in sums.items() if v}


def check_history(
    responses: Mapping[str, AnalysisData],
    completeness: Iterable[DayCompleteness],
    stored: Mapping[tuple[str, date], float],
    *,
    site: str = "",
    released: Iterable[tuple[str, date]] = (),
    sensitive: Iterable[object] = (),
) -> ValidationResult:
    """H1 (retroactive zeroing) and H2 (large corrections) for one chunk.

    ``stored`` maps (statistic key, local day) -> the day total already in the
    recorder (only days with stored rows). ``released`` lists days the user
    released with ``eg_austria.release_held``; they are not held again.
    """

    released_set = set(released)
    result = ValidationResult()
    for (stat, day), (total, all_zero) in sorted(
        day_totals(responses, completeness, site=site).items()
    ):
        old = stored.get((stat, day))
        if old is None:
            continue
        if all_zero and old != 0 and (stat, day) not in released_set:
            result.held_days.add((stat, day))
            result.findings.append(
                Finding(
                    rule="H1",
                    severity=Severity.BLOCKING,
                    message=redact_text(
                        f"H1 {day.isoformat()}: {stat} stored with a non-zero "
                        "total, now every hour 0; day held",
                        sensitive,
                    ),
                    statistic=stat,
                    day=day,
                )
            )
            continue
        change = abs(total - old)
        relative = None if old == 0 else (total - old) / abs(old)
        if change > H2_MIN_KWH and (
            relative is None or abs(relative) > H2_MIN_RELATIVE
        ):
            result.corrections.append(Correction(day, stat, relative))
    return result
