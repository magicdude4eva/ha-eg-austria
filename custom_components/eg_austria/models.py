"""Response models and parsers for the EG Austria portal API (spec 01).

No Home Assistant imports, so the parsers can be tested alone. Parsers raise
``SchemaError`` (spec 05 stage 1: rules S1-S4, S9) with the key path of the
offending value. Everything after parsing (S5-S8, plausibility) is
``validation.py``'s job.

Rules that apply to every model:

* numeric energy fields are ``float | None``; JSON ints are converted, bools
  and strings are rejected;
* keys this module does not know are kept in ``extra`` (names and values);
  ``extra_keys()`` lists only the names, for diagnostics (``schema_extra``);
* sensitive fields (names, addresses) are kept out of ``repr``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from .exceptions import SchemaError


class Aggregation(StrEnum):
    """``aggregation`` values accepted by ``analysis-data``."""

    MONTH = "Month"
    WEEK = "Week"
    DAY = "Day"
    HOUR = "Hour"
    FIFTEEN_MINUTES = "FifteenMinutes"


class Direction(StrEnum):
    """Metering point direction (``consumptionOrGeneration``)."""

    CONSUMPTION = "Consumption"
    GENERATION = "Generation"


# ---------------------------------------------------------------------------
# Low-level readers


def _key(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _require_dict(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError("S1", path, f"expected object, got {type(value).__name__}")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError("S1", path, f"expected list, got {type(value).__name__}")
    return value


def _get(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise SchemaError("S2", _key(path, key), "required key missing")
    return obj[key]


def _number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError("S3", path, f"expected number, got {type(value).__name__}")
    return float(value)


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError("S3", path, f"expected bool, got {type(value).__name__}")
    return value


def _opt_bool(value: Any, path: str) -> bool | None:
    return None if value is None else _bool(value, path)


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise SchemaError("S3", path, f"expected string, got {type(value).__name__}")
    return value


def _opt_str(value: Any, path: str) -> str | None:
    return None if value is None else _str(value, path)


def _label_str(value: Any) -> str | None:
    """Lenient: label-only fields never fail validation (issue #19)."""

    return value if isinstance(value, str) and value else None


def _label_number(value: Any) -> float | None:
    """Lenient: label-only fields never fail validation (issue #19)."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError("S3", path, f"expected integer, got {type(value).__name__}")
    return value


def _utc(value: Any, path: str) -> datetime:
    """Parse an ISO 8601 timestamp with ``Z`` or offset and convert to UTC (S4)."""

    text = _str(value, path)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as err:
        raise SchemaError("S4", path, "not an ISO 8601 timestamp") from err
    if parsed.tzinfo is None:
        raise SchemaError("S4", path, "timestamp has no offset")
    return parsed.astimezone(UTC)


def _date(value: Any, path: str, rule: str = "S3") -> date:
    text = _str(value, path)
    try:
        return date.fromisoformat(text)
    except ValueError as err:
        raise SchemaError(rule, path, "not a YYYY-MM-DD date") from err


def _opt_date(value: Any, path: str) -> date | None:
    return None if value is None else _date(value, path)


def _extra(obj: Mapping[str, Any], known: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if k not in known}


# ---------------------------------------------------------------------------
# analysis-data

# Per-direction point fields: (required numbers, required flags, optional
# numbers, optional flags, ignored keys).
_POINT_FIELDS: dict[Direction, dict[str, tuple[str, ...]]] = {
    Direction.CONSUMPTION: {
        "numbers": ("consumption", "consumptionFromCommunity", "consumptionInExcess"),
        "flags": ("consumptionFaulty", "consumptionFromCommunityFaulty"),
        "opt_numbers": (
            "consumptionComplete",
            "consumptionFromCommunityComplete",
            "consumptionInExcessComplete",
            "consumptionByFactor",
            "consumptionByFactorComplete",
        ),
        "opt_flags": ("consumptionByFactorFaulty",),
        "ignored": (
            "date",
            "energyMix",
            "energyMixComplete",
            "consumptionFromCommunityPercent",
            "consumptionFromCommunityPercentComplete",
            "actualConsumptionPartFactor",
            "actualConsumptionPartFactorComplete",
            "spendings",
            "spendingsComplete",
        ),
    },
    Direction.GENERATION: {
        "numbers": ("generation", "generationToCommunity", "generationInExcess"),
        "flags": ("generationFaulty", "generationToCommunityFaulty"),
        "opt_numbers": (
            "generationComplete",
            "generationToCommunityComplete",
            "generationInExcessComplete",
            "generationByFactor",
            "generationByFactorComplete",
        ),
        "opt_flags": ("generationByFactorFaulty",),
        "ignored": (
            "date",
            "generationToCommunityPercent",
            "generationToCommunityPercentComplete",
            "actualGenerationPartFactor",
            "actualGenerationPartFactorComplete",
            "earnings",
            "earningsComplete",
        ),
    },
}


def _known_point_keys(direction: Direction) -> frozenset[str]:
    spec = _POINT_FIELDS[direction]
    return frozenset(
        (
            "dateTimeFrom",
            "dateTimeFromLocal",
            *(k for keys in spec.values() for k in keys),
        )
    )


_KNOWN_POINT_KEYS = {d: _known_point_keys(d) for d in Direction}


@dataclass(frozen=True, slots=True)
class EnergyPoint:
    """One ``consumptionDataPoints`` or ``generationDataPoints`` entry.

    Field names are direction-neutral: ``value`` is ``consumption`` or
    ``generation``, ``community`` is ``consumptionFromCommunity`` or
    ``generationToCommunity``, ``excess`` is ``…InExcess``.

    Missing meter data arrives as ``0`` with ``faulty`` true (G1); the parser
    keeps both, spec 04 / ADR 0005 decide what is unknown.
    """

    direction: Direction
    start: datetime  # dateTimeFrom, UTC
    value: float | None
    community: float | None
    excess: float | None
    faulty: bool
    community_faulty: bool
    value_complete: float | None = None
    community_complete: float | None = None
    excess_complete: float | None = None
    by_factor: float | None = None
    by_factor_complete: float | None = None
    by_factor_faulty: bool | None = None
    # Local wall time with a misleading "Z"; kept raw, never parsed or used as key.
    local_wall_time: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)


def _parse_point(raw: Any, direction: Direction, path: str) -> EnergyPoint:
    obj = _require_dict(raw, path)
    spec = _POINT_FIELDS[direction]
    value, community, excess = (
        _number(_get(obj, k, path), _key(path, k)) for k in spec["numbers"]
    )
    faulty, community_faulty = (
        _bool(_get(obj, k, path), _key(path, k)) for k in spec["flags"]
    )
    value_c, community_c, excess_c, by_factor, by_factor_c = (
        _number(obj.get(k), _key(path, k)) for k in spec["opt_numbers"]
    )
    (by_factor_faulty,) = (
        _opt_bool(obj.get(k), _key(path, k)) for k in spec["opt_flags"]
    )
    return EnergyPoint(
        direction=direction,
        start=_utc(_get(obj, "dateTimeFrom", path), _key(path, "dateTimeFrom")),
        value=value,
        community=community,
        excess=excess,
        faulty=faulty,
        community_faulty=community_faulty,
        value_complete=value_c,
        community_complete=community_c,
        excess_complete=excess_c,
        by_factor=by_factor,
        by_factor_complete=by_factor_c,
        by_factor_faulty=by_factor_faulty,
        local_wall_time=_opt_str(
            obj.get("dateTimeFromLocal"), _key(path, "dateTimeFromLocal")
        ),
        extra=_extra(obj, _KNOWN_POINT_KEYS[direction]),
    )


_TOTALS = (
    "totalConsumption",
    "totalGeneration",
    "totalConsumptionFromCommunity",
    "totalGenerationToCommunity",
    "totalConsumptionInExcess",
    "totalGenerationInExcess",
)
_DAY_FLAGS = (
    "consumptionFaulty",
    "consumptionFromCommunityFaulty",
    "consumptionInExcessFaulty",
    "generationFaulty",
    "generationToCommunityFaulty",
    "generationInExcessFaulty",
)
_POINT_LISTS = {
    "consumptionDataPoints": Direction.CONSUMPTION,
    "generationDataPoints": Direction.GENERATION,
}
# Known but ignored top-level keys: other totals, by-factor, complete, percent,
# part factor, energy mix, earnings/spendings and further faulty flags.
_IGNORED_TOP_PREFIXES = ("total",)
_IGNORED_TOP_SUFFIXES = ("Faulty",)


def _top_key_known(key: str) -> bool:
    return (
        key in _TOTALS
        or key in _DAY_FLAGS
        or key in _POINT_LISTS
        or key.startswith(_IGNORED_TOP_PREFIXES)
        or key.endswith(_IGNORED_TOP_SUFFIXES)
    )


@dataclass(frozen=True, slots=True)
class AnalysisTotals:
    """The six ``total*`` fields used for the sum check (P5)."""

    consumption: float | None
    generation: float | None
    consumption_from_community: float | None
    generation_to_community: float | None
    consumption_in_excess: float | None
    generation_in_excess: float | None


@dataclass(frozen=True, slots=True)
class AnalysisFlags:
    """Response-level ``*Faulty`` flags (diagnostics only)."""

    consumption: bool
    consumption_from_community: bool
    consumption_in_excess: bool
    generation: bool
    generation_to_community: bool
    generation_in_excess: bool


@dataclass(frozen=True, slots=True)
class AnalysisData:
    """Parsed ``analysis-data`` response; point lists sorted ascending by start."""

    consumption: tuple[EnergyPoint, ...]
    generation: tuple[EnergyPoint, ...]
    totals: AnalysisTotals
    flags: AnalysisFlags
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_empty(self) -> bool:
        """Return True for the valid empty response (empty lists, 1140/20)."""

        return not self.consumption and not self.generation

    def points(self, direction: Direction) -> tuple[EnergyPoint, ...]:
        """Return the point list of one direction."""

        return (
            self.consumption if direction is Direction.CONSUMPTION else self.generation
        )

    def extra_keys(self) -> set[str]:
        """Names of unknown keys (``schema_extra``), never their values."""

        keys = set(self.extra)
        for name, direction in _POINT_LISTS.items():
            for point in self.points(direction):
                keys.update(f"{name}[].{k}" for k in point.extra)
        return keys


def _direction_of(key: str) -> Direction:
    return (
        Direction.GENERATION if "generation" in key.lower() else Direction.CONSUMPTION
    )


def _get_unless_empty(
    obj: Mapping[str, Any], key: str, empty: set[Direction], default: Any = None
) -> Any:
    """``_get()``, but ``default`` for a missing key of a direction without points."""

    if key not in obj and _direction_of(key) in empty:
        return default
    return _get(obj, key, "")


def parse_analysis(raw: Any) -> AnalysisData:
    """Parse an ``analysis-data`` response."""

    obj = _require_dict(raw, "")
    lists: dict[Direction, tuple[EnergyPoint, ...]] = {}
    for name, direction in _POINT_LISTS.items():
        items = _require_list(_get(obj, name, ""), name)
        points = [
            _parse_point(item, direction, f"{name}[{i}]")
            for i, item in enumerate(items)
        ]
        lists[direction] = tuple(sorted(points, key=lambda p: p.start))
    # Issue #78: the portal leaves out the totals (and may leave out the day
    # flags) of a direction without any data point, for example a site
    # without generation, so there a missing key is null / not faulty. With
    # points, a missing key is still S2: that is the reduced body sent for a
    # day outside a community's activation (issue #16).
    empty = {d for d, points in lists.items() if not points}
    totals = AnalysisTotals(
        *(_number(_get_unless_empty(obj, k, empty), k) for k in _TOTALS)
    )
    flags = AnalysisFlags(
        *(_bool(_get_unless_empty(obj, k, empty, False), k) for k in _DAY_FLAGS)
    )
    return AnalysisData(
        consumption=lists[Direction.CONSUMPTION],
        generation=lists[Direction.GENERATION],
        totals=totals,
        flags=flags,
        extra={k: v for k, v in obj.items() if not _top_key_known(k)},
    )


# ---------------------------------------------------------------------------
# day-completeness


@dataclass(frozen=True, slots=True)
class DayCompleteness:
    """One ``day-completeness`` entry. ``null`` (future days) is "not complete"."""

    day: date
    consumption_complete: bool
    generation_complete: bool
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def complete(self, direction: Direction) -> bool:
        """Return the flag of one direction."""

        if direction is Direction.CONSUMPTION:
            return self.consumption_complete
        return self.generation_complete


_COMPLETENESS_KEYS = frozenset(("date", "consumptionComplete", "generationComplete"))


def parse_day_completeness(raw: Any) -> list[DayCompleteness]:
    """Parse a ``day-completeness`` response, sorted ascending by date.

    The flags here are booleans (or null); a number is a schema error, unlike
    the numeric ``*Complete`` fields of ``analysis-data``.
    """

    result = []
    for i, item in enumerate(_require_list(raw, "")):
        path = f"[{i}]"
        obj = _require_dict(item, path)
        result.append(
            DayCompleteness(
                day=_date(_get(obj, "date", path), _key(path, "date"), rule="S9"),
                consumption_complete=bool(
                    _opt_bool(
                        _get(obj, "consumptionComplete", path),
                        _key(path, "consumptionComplete"),
                    )
                ),
                generation_complete=bool(
                    _opt_bool(
                        _get(obj, "generationComplete", path),
                        _key(path, "generationComplete"),
                    )
                ),
                extra=_extra(obj, _COMPLETENESS_KEYS),
            )
        )
    return sorted(result, key=lambda d: d.day)


# ---------------------------------------------------------------------------
# metering-point (adapter, spec 01 "MeteringPointInfo")


@dataclass(frozen=True, slots=True)
class Activation:
    """A metering point's membership in one energy community."""

    community_id: int  # energyCommunityId, used as energyCommunityIds
    community_type: str  # "CC" (BEG), "RC_R" (regional EEG), others as sent
    active_from: date
    active_to: date | None
    status: str
    community_name: str = field(repr=False)  # sensitive
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)
    # Config-flow label only (issue #19); never stored.
    active_part_factor: float | None = None


@dataclass(frozen=True, slots=True)
class MeteringPoint:
    """One metering point of a location."""

    id: int  # used as mpIds
    direction: Direction
    activations: tuple[Activation, ...]
    number_tail: str = field(repr=False)  # last 4 characters, labels only
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)
    # Config-flow labels only (issue #19): never logged, stored or diagnosed.
    number: str = field(default="", repr=False)
    name: str | None = field(default=None, repr=False)
    source_of_production: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class Location:
    """One location (address) with its metering points."""

    id: str
    metering_points: tuple[MeteringPoint, ...]
    # street + house number; config-flow select only, never logged.
    label: str = field(repr=False)
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class MeteringPointInfo:
    """Parsed ``metering-point`` response."""

    locations: tuple[Location, ...]

    def location(self, location_id: str) -> Location | None:
        """Return the location with this id, if present."""

        return next((loc for loc in self.locations if loc.id == location_id), None)

    def iter_activations(
        self, location_id: str | None = None
    ) -> Iterator[tuple[MeteringPoint, Activation]]:
        """Yield every (metering point, activation), optionally for one location."""

        for loc in self.locations:
            if location_id is not None and loc.id != location_id:
                continue
            for mp in loc.metering_points:
                for act in mp.activations:
                    yield mp, act


_ACTIVATION_KEYS = frozenset(
    (
        "energyCommunityId",
        "energyCommunityName",
        "energyCommunityType",
        "status",
        "begin",
        "end",
    )
)
_MP_KEYS = frozenset(("id", "number", "consumptionOrGeneration", "activations"))
_LOCATION_KEYS = frozenset(("locationId", "memberMeteringpoints"))


def _parse_activation(raw: Any, path: str) -> Activation:
    obj = _require_dict(raw, path)
    return Activation(
        community_id=_int(
            _get(obj, "energyCommunityId", path), _key(path, "energyCommunityId")
        ),
        community_name=_str(
            _get(obj, "energyCommunityName", path), _key(path, "energyCommunityName")
        ),
        community_type=_str(
            _get(obj, "energyCommunityType", path), _key(path, "energyCommunityType")
        ),
        active_from=_date(_get(obj, "begin", path), _key(path, "begin")),
        active_to=_opt_date(_get(obj, "end", path), _key(path, "end")),
        status=_str(_get(obj, "status", path), _key(path, "status")),
        extra=_extra(obj, _ACTIVATION_KEYS),
        active_part_factor=_label_number(obj.get("activePartFactor")),
    )


def _parse_metering_point(raw: Any, path: str) -> MeteringPoint:
    obj = _require_dict(raw, path)
    direction_raw = _str(
        _get(obj, "consumptionOrGeneration", path),
        _key(path, "consumptionOrGeneration"),
    )
    try:
        direction = Direction(direction_raw)
    except ValueError as err:
        raise SchemaError(
            "S3", _key(path, "consumptionOrGeneration"), "unknown direction"
        ) from err
    activations = _require_list(
        _get(obj, "activations", path), _key(path, "activations")
    )
    number = _str(_get(obj, "number", path), _key(path, "number"))
    return MeteringPoint(
        id=_int(_get(obj, "id", path), _key(path, "id")),
        number=number,
        number_tail=number[-4:],
        name=_label_str(obj.get("name")),
        source_of_production=_label_str(obj.get("sourceOfProduction")),
        direction=direction,
        activations=tuple(
            _parse_activation(a, f"{path}.activations[{i}]")
            for i, a in enumerate(activations)
        ),
        extra=_extra(obj, _MP_KEYS),
    )


def _location_label(obj: Mapping[str, Any], path: str) -> str:
    address = obj.get("address")
    if not isinstance(address, dict):
        return ""
    parts = (
        _opt_str(address.get(k), _key(f"{path}.address", k))
        for k in ("street", "houseNumber")
    )
    return " ".join(p for p in parts if p)


def parse_metering_points(raw: Any) -> MeteringPointInfo:
    """Map the ``metering-point`` location list into ``MeteringPointInfo``.

    This is the one adapter function for that endpoint's shape; participation
    factors and Zero plans stay in ``extra`` (snapshot values, never config).
    """

    locations = []
    for i, item in enumerate(_require_list(raw, "")):
        path = f"[{i}]"
        obj = _require_dict(item, path)
        mps = _require_list(
            _get(obj, "memberMeteringpoints", path), _key(path, "memberMeteringpoints")
        )
        locations.append(
            Location(
                id=_str(_get(obj, "locationId", path), _key(path, "locationId")),
                label=_location_label(obj, path),
                metering_points=tuple(
                    _parse_metering_point(mp, f"{path}.memberMeteringpoints[{j}]")
                    for j, mp in enumerate(mps)
                ),
                extra=_extra(obj, _LOCATION_KEYS),
            )
        )
    return MeteringPointInfo(locations=tuple(locations))


# ---------------------------------------------------------------------------
# portal-session


@dataclass(frozen=True, slots=True)
class Member:
    """One member the token may act for."""

    member_id: str  # the {member} path value
    must_accept_platform_terms: bool
    # Config-flow select label only (spec 02): never stored, logged or diagnosed.
    display_name: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Parsed ``portal-session``; ``email`` is dropped at parse time."""

    members: tuple[Member, ...]

    def member_ids(self) -> list[str]:
        """Return the member numbers in portal order."""

        return [m.member_id for m in self.members]


def parse_session(raw: Any) -> SessionInfo:
    """Parse ``GET portal-session``."""

    obj = _require_dict(raw, "")
    members = []
    for i, item in enumerate(_require_list(_get(obj, "members", ""), "members")):
        path = f"members[{i}]"
        m = _require_dict(item, path)
        member_id = _get(m, "memberId", path)
        if isinstance(member_id, int) and not isinstance(member_id, bool):
            member_id = str(member_id)
        members.append(
            Member(
                member_id=_str(member_id, _key(path, "memberId")),
                must_accept_platform_terms=bool(
                    _opt_bool(
                        m.get("mustAcceptPlatformTerms"),
                        _key(path, "mustAcceptPlatformTerms"),
                    )
                ),
                display_name=_opt_str(m.get("displayName"), _key(path, "displayName"))
                or "",
            )
        )
    return SessionInfo(members=tuple(members))


# ---------------------------------------------------------------------------
# maintenance

# Entry field names are still [verify] (no window announced at G1); accept the
# likely spellings so a first real window does not break the run.
_MAINT_START = ("start", "startDate", "startTime", "from")
_MAINT_END = ("end", "endDate", "endTime", "to")


@dataclass(frozen=True, slots=True)
class Maintenance:
    """An announced maintenance window (UTC)."""

    start: datetime
    end: datetime
    title: str | None = field(default=None, repr=False)
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def active_at(self, now: datetime, margin_seconds: float = 0) -> bool:
        """Return True if ``now`` is within the window, widened by the margin."""

        delta = now - self.start
        return (
            -margin_seconds <= delta.total_seconds()
            and (now - self.end).total_seconds() <= margin_seconds
        )


def _first_key(obj: Mapping[str, Any], names: tuple[str, ...], path: str) -> str:
    for name in names:
        if name in obj:
            return name
    raise SchemaError("S2", _key(path, names[0]), "required key missing")


def parse_maintenance(raw: Any) -> list[Maintenance]:
    """Parse ``maintenance``; ``None`` (empty body, G1) is an empty list."""

    if raw is None:
        return []
    result = []
    for i, item in enumerate(_require_list(raw, "")):
        path = f"[{i}]"
        obj = _require_dict(item, path)
        start_key = _first_key(obj, _MAINT_START, path)
        end_key = _first_key(obj, _MAINT_END, path)
        result.append(
            Maintenance(
                start=_utc(obj[start_key], _key(path, start_key)),
                end=_utc(obj[end_key], _key(path, end_key)),
                title=_opt_str(obj.get("title"), _key(path, "title")),
                extra=_extra(obj, frozenset((start_key, end_key, "title"))),
            )
        )
    return result


# ---------------------------------------------------------------------------
# version


@dataclass(frozen=True, slots=True)
class PortalVersion:
    """``GET /v1/version``."""

    git_commit: str
    build_time: str
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)


def parse_version(raw: Any) -> PortalVersion:
    """Parse ``version``."""

    obj = _require_dict(raw, "")
    return PortalVersion(
        git_commit=_str(_get(obj, "gitCommit", ""), "gitCommit"),
        build_time=_str(_get(obj, "buildTime", ""), "buildTime"),
        extra=_extra(obj, frozenset(("gitCommit", "buildTime"))),
    )
