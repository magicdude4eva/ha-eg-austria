"""Monthly settlement from ``billing/transactions`` (issue #74, ADR 0008).

The portal's hourly ``earnings`` / ``spendings`` are standard-rate amounts
(Zero not considered). The invoices in ``billing/transactions`` are the
truth per community, direction and billed month. Once a month is settled,
its provisional hours are scaled per community and direction so the month
total equals the settlement; hours stay an approximation, months match the
invoices.

Rows:

* ``Einspeisevergütung <Monat Jahr>`` → feed-in (generation), amount as is;
* ``Strombezug <Monat Jahr>`` → consumption, the charged amount (negative)
  becomes a positive cost;
* either may end with ``(Zero-Tarif-Korrektur folgt)``: still a settlement,
  a correction may follow;
* ``Energierechnungskorrektur <Monat Jahr>`` has no direction: it negates an
  earlier row of the same community and month exactly and takes that row's
  direction. A direction whose rows are all reversed is unsettled until it is
  billed again; a 0.00 correction is ignored. A correction that matches no
  row leaves the month (both directions) unsettled;
* rows without a community (``Mitgliedsbeitrag``, ``Einzahlung``) and rows of
  communities the entry doesn't select are ignored; other descriptions are
  counted as unknown and ignored.

Communities are matched by name (``energyCommunityShortName`` equals the
community name of ``metering-point``); the transactions carry no id.

Invoices are per member, community and direction, and carry no metering
point. When another metering point of the member in that direction (at any
location) is active in the community during the month and the entry doesn't
select it, the invoice covers it too (``other_points``, issue #104). Such
a month is scaled by the member's provisional total over all those points
instead (``member_totals()``, one extra ``Month`` request per community,
issue #115): the entry gets its share of the invoice, in proportion to its
portal amounts. Without that total the month stays provisional.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
import hashlib
import re
from typing import Final

from .models import (
    AnalysisData,
    Direction,
    EnergyPoint,
    MeteringPointInfo,
    Transaction,
)
from .statistics import local_date

MONTHS: Final = {
    "jänner": 1,
    "januar": 1,
    "jaenner": 1,
    "februar": 2,
    "feber": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}
_ROW = re.compile(
    r"^\s*(Einspeisevergütung|Strombezug|Energierechnungskorrektur)\s+"
    r"(\S+)\s+(\d{4})\b",
    re.IGNORECASE,
)
_KIND: Final = {
    "einspeisevergütung": Direction.GENERATION,
    "strombezug": Direction.CONSUMPTION,
}
# Amounts are compared in cents.
_CENT: Final = 100


class MonthState(StrEnum):
    """Settlement state of one community, direction and month."""

    SETTLED = "settled"
    UNMATCHED_CORRECTION = "unmatched_correction"
    ZERO_PROVISIONAL = "zero_provisional"  # settled, but nothing to scale
    OTHER_POINTS = "other_points"  # invoice also covers unselected points
    # Shared invoice, scaled by the member's total over all points (#115).
    SETTLED_SHARED = "settled_shared"


Month = tuple[int, int]  # (year, month)
Key = tuple[str, Direction, Month]  # (community slug, direction, month)


@dataclass(slots=True)
class Settlements:
    """Settled totals per community, direction and month (EUR, positive)."""

    amounts: dict[Key, float] = field(default_factory=dict)
    unmatched: set[tuple[str, Month]] = field(default_factory=set)
    # Keys whose invoice also covers metering points the entry doesn't select.
    shared: set[Key] = field(default_factory=set)
    # Those unselected metering point ids per shared key (issue #115).
    others: dict[Key, set[int]] = field(default_factory=dict)
    unknown_rows: int = 0
    ignored_rows: int = 0

    def settled(self, key: Key) -> float | None:
        """Return the settled total, or None while the month is unsettled."""

        slug, _, month = key
        if (slug, month) in self.unmatched or key in self.shared:
            return None
        return self.amounts.get(key)

    def months(self) -> set[Month]:
        """Every month with a settlement or an unmatched correction."""

        return {k[2] for k in self.amounts} | {m for _, m in self.unmatched}

    def digests(self) -> dict[str, str]:
        """Value-free fingerprint per key, to detect changed settlements."""

        out: dict[str, str] = {}
        for (slug, direction, (year, month)), amount in sorted(
            self.amounts.items(), key=lambda kv: (kv[0][0], kv[0][1].value, kv[0][2])
        ):
            unmatched = (slug, (year, month)) in self.unmatched
            shared = (slug, direction, (year, month)) in self.shared
            text = (
                f"{round(amount * _CENT)}{'u' if unmatched else ''}"
                f"{'s' if shared else ''}"
            )
            out[f"{slug}/{direction.value}/{year:04d}-{month:02d}"] = hashlib.sha256(
                text.encode()
            ).hexdigest()[:12]
        for slug, (year, month) in sorted(self.unmatched):
            for direction in Direction:
                out.setdefault(
                    f"{slug}/{direction.value}/{year:04d}-{month:02d}", "unmatched"
                )
        return out


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def billed_month(description: str) -> tuple[str, Month] | None:
    """Return (kind, (year, month)) of a community energy row, else None."""

    match = _ROW.match(description)
    if match is None:
        return None
    month = MONTHS.get(match.group(2).casefold().rstrip(".,"))
    if month is None:
        return None
    return match.group(1).casefold(), (int(match.group(3)), month)


def build_settlements(
    transactions: Iterable[Transaction], names: Mapping[str, str]
) -> Settlements:
    """Group the rows per selected community (``names``: slug -> portal name)."""

    by_name = {_norm(name): slug for slug, name in names.items() if name}
    result = Settlements()
    rows: dict[tuple[str, Month], list[tuple[Direction, int]]] = defaultdict(list)
    corrections: list[tuple[str, Month, int]] = []
    # Oldest first, so a correction always follows the row it negates.
    for tx in sorted(transactions, key=lambda t: t.date):
        if tx.community_name is None:
            result.ignored_rows += 1
            continue
        slug = by_name.get(_norm(tx.community_name))
        if slug is None:
            result.ignored_rows += 1
            continue
        parsed = billed_month(tx.description)
        if parsed is None:
            result.unknown_rows += 1
            continue
        kind, month = parsed
        cents = round(tx.amount * _CENT)
        if kind in _KIND:
            rows[slug, month].append((_KIND[kind], cents))
        elif cents:  # a 0.00 correction changes nothing (#103)
            corrections.append((slug, month, cents))
    for slug, month, cents in corrections:
        candidates = [d for d, c in rows[slug, month] if c == -cents]
        if len(set(candidates)) != 1:
            # No row, or both directions with that amount: can't assign it.
            result.unmatched.add((slug, month))
            continue
        # The reversed row is gone. A direction without any row left is
        # unsettled until the new invoice arrives, not settled with 0 (#103).
        rows[slug, month].remove((candidates[0], -cents))
    for (slug, month), entries in rows.items():
        totals: dict[Direction, int] = defaultdict(int)
        for direction, cents in entries:
            totals[direction] += cents
        for direction, cents in totals.items():
            # Positive amounts: income for feed-in, cost for consumption.
            sign = 1 if direction is Direction.GENERATION else -1
            result.amounts[slug, direction, month] = sign * cents / _CENT
    return result


def mark_other_points(
    settlements: Settlements,
    info: MeteringPointInfo,
    mp_ids: Collection[int],
    slugs: Mapping[int, str],
) -> None:
    """Flag settled keys whose invoice covers unselected points (issue #104).

    ``slugs`` maps the selected community ids to their slugs. A point counts
    when the entry doesn't select it, it has the key's direction and one of
    its activations in that community overlaps the month (any location of
    the member; a withdrawn activation, ending before it starts, doesn't).
    """

    selected = set(mp_ids)
    ranges: dict[tuple[str, Direction], list[tuple[date, date | None, int]]] = (
        defaultdict(list)
    )
    for loc in info.locations:
        for mp in loc.metering_points:
            if mp.id in selected:
                continue
            for act in mp.activations:
                slug = slugs.get(act.community_id)
                if slug is None:
                    continue
                if act.active_to is not None and act.active_to < act.active_from:
                    continue
                ranges[slug, mp.direction].append(
                    (act.active_from, act.active_to, mp.id)
                )
    for key in settlements.amounts:
        slug, direction, month = key
        begin, end = first_day(month), _month_end(month)
        ids = {
            mp_id
            for start, stop, mp_id in ranges.get((slug, direction), ())
            if start <= end and (stop is None or stop >= begin)
        }
        if ids:
            settlements.shared.add(key)
            settlements.others[key] = ids


@dataclass(frozen=True, slots=True)
class SharedRequest:
    """One ``Month`` request for the member's total in a community (#115)."""

    slug: str
    first: date
    last: date
    mp_ids: tuple[int, ...]  # the entry's points plus the unselected ones


def shared_requests(
    settlements: Settlements,
    hourly: Mapping[str, Iterable[AnalysisData]],
    mp_ids: Collection[int],
) -> list[SharedRequest]:
    """Plan one request per community with a shared month in ``hourly``.

    Only months this run fetched count (the window holds whole settled
    months). A request spans the first to the last such month of the
    community; months in between that aren't shared are ignored.
    """

    seen: set[tuple[str, Month]] = set()
    for slug, responses in hourly.items():
        for data in responses:
            for direction in Direction:
                seen.update((slug, _month(p)) for p in data.points(direction))
    months: dict[str, list[Month]] = defaultdict(list)
    ids: dict[str, set[int]] = defaultdict(set)
    for key in settlements.shared:
        slug, _, month = key
        if (slug, month) not in seen or (slug, month) in settlements.unmatched:
            continue
        months[slug].append(month)
        ids[slug] |= settlements.others.get(key, set())
    return [
        SharedRequest(
            slug,
            first_day(min(items)),
            _month_end(max(items)),
            tuple(sorted(set(mp_ids) | ids[slug])),
        )
        for slug, items in sorted(months.items())
    ]


@dataclass(frozen=True, slots=True)
class MonthTotal:
    """Provisional kWh and € of one community, direction and month."""

    kwh: float
    money: float


def member_totals(slug: str, data: AnalysisData) -> dict[Key, MonthTotal]:
    """Sum a response per direction and local month (any aggregation)."""

    kwh: dict[Key, float] = defaultdict(float)
    money: dict[Key, float] = defaultdict(float)
    for direction in Direction:
        for point in data.points(direction):
            key = (slug, direction, _month(point))
            kwh[key] += point.value or 0.0
            money[key] += point.money or 0.0
    return {key: MonthTotal(kwh[key], money[key]) for key in kwh}


def _month(point: EnergyPoint) -> Month:
    day = local_date(point.start)
    return day.year, day.month


@dataclass(frozen=True, slots=True)
class MonthFactor:
    """How one community, direction and month was scaled."""

    state: MonthState
    factor: float | None


def _month_sums(
    hourly: Mapping[str, Iterable[AnalysisData]],
) -> tuple[dict[Key, float], dict[Key, float], set[Key]]:
    money: dict[Key, float] = defaultdict(float)
    kwh: dict[Key, float] = defaultdict(float)
    seen: set[Key] = set()
    for slug, responses in hourly.items():
        for data in responses:
            for direction in Direction:
                for point in data.points(direction):
                    key = (slug, direction, _month(point))
                    seen.add(key)
                    if point.money is not None:
                        money[key] += point.money
                    kwh[key] += point.value or 0.0
    return money, kwh, seen


# Below this, "no difference" (kWh and EUR, summed over a month).
_EPS: Final = 1e-6


def _shared_factor(
    settled: float,
    own_portal: float,
    own_kwh: float,
    own_scaled: float,
    total: MonthTotal | None,
) -> MonthFactor:
    """Scale a shared month to the entry's share of the invoice (#115).

    share = invoice * own portal € / member portal €; the factor applies to
    the entry's hours as they are now (after Zero repricing), so the month
    ends at that share. Stays provisional without a member total, or when
    the other points add nothing to it (a portal that dropped them must
    never bring back the whole invoice, #104).
    """

    if total is None:
        return MonthFactor(MonthState.OTHER_POINTS, None)
    if total.kwh <= own_kwh + _EPS or abs(own_portal) > abs(total.money) + _EPS:
        return MonthFactor(MonthState.OTHER_POINTS, None)
    if abs(total.money) < 1e-9:
        if abs(settled) < 1e-9:
            return MonthFactor(MonthState.SETTLED_SHARED, 1.0)
        return MonthFactor(MonthState.ZERO_PROVISIONAL, None)
    share = settled * own_portal / total.money
    if abs(own_scaled) < 1e-9:
        if abs(share) < 1e-9:
            return MonthFactor(MonthState.SETTLED_SHARED, 1.0)
        return MonthFactor(MonthState.ZERO_PROVISIONAL, None)
    return MonthFactor(MonthState.SETTLED_SHARED, share / own_scaled)


def month_factors(
    hourly: Mapping[str, Iterable[AnalysisData]],
    settlements: Settlements,
    totals: Mapping[Key, MonthTotal] | None = None,
    portal: Mapping[str, Iterable[AnalysisData]] | None = None,
) -> dict[Key, MonthFactor]:
    """Scale factor per settled key from the provisional hourly amounts.

    ``totals`` are the member's provisional totals of shared months
    (``member_totals()``, #115); ``portal`` the entry's hours with the
    portal's amounts (before Zero repricing), default ``hourly``.
    """

    provisional, _, seen = _month_sums(hourly)
    own_portal, own_kwh, _ = _month_sums(portal if portal is not None else hourly)
    result: dict[Key, MonthFactor] = {}
    for key in seen:
        slug, _, month = key
        if (slug, month) in settlements.unmatched:
            result[key] = MonthFactor(MonthState.UNMATCHED_CORRECTION, None)
            continue
        settled = settlements.amounts.get(key)
        if settled is None:
            continue
        if key in settlements.shared:
            result[key] = _shared_factor(
                settled,
                own_portal.get(key, 0.0),
                own_kwh.get(key, 0.0),
                provisional.get(key, 0.0),
                (totals or {}).get(key),
            )
            continue
        total = provisional.get(key, 0.0)
        if abs(total) < 1e-9:
            if abs(settled) < 1e-9:
                result[key] = MonthFactor(MonthState.SETTLED, 1.0)
            else:
                result[key] = MonthFactor(MonthState.ZERO_PROVISIONAL, None)
            continue
        result[key] = MonthFactor(MonthState.SETTLED, settled / total)
    return result


def _scale(
    data: AnalysisData, slug: str, factors: Mapping[Key, MonthFactor]
) -> AnalysisData:
    def points(direction: Direction) -> tuple[EnergyPoint, ...]:
        out = []
        for point in data.points(direction):
            entry = factors.get((slug, direction, _month(point)))
            if (
                entry is not None
                and entry.factor is not None
                and point.money is not None
            ):
                point = replace(point, money=point.money * entry.factor)
            out.append(point)
        return tuple(out)

    return replace(
        data,
        consumption=points(Direction.CONSUMPTION),
        generation=points(Direction.GENERATION),
    )


def apply_settlements(
    responses: Mapping[str, list[AnalysisData]], factors: Mapping[Key, MonthFactor]
) -> dict[str, list[AnalysisData]]:
    """Return the responses with settled months' amounts scaled."""

    if not factors:
        return {slug: list(items) for slug, items in responses.items()}
    return {
        slug: [_scale(d, slug, factors) for d in items]
        for slug, items in responses.items()
    }


def first_day(month: Month) -> date:
    """Return the first day of a (year, month)."""

    return date(month[0], month[1], 1)


def changed_months(old: Mapping[str, str], new: Mapping[str, str]) -> set[Month]:
    """Months whose settlement digest appeared, changed or disappeared."""

    months: set[Month] = set()
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            year, month = key.rsplit("/", 1)[1].split("-")
            months.add((int(year), int(month)))
    return months


def settled_window_start(
    start: date,
    end: date,
    settlements: Settlements,
    changed: Iterable[Month],
    floor: date | None = None,
) -> date:
    """Move ``start`` so every settled month in the window is whole.

    A settled month that only partly lies in the window starts at its first
    day; a month whose settlement changed since the last run is re-fetched
    from its first day, even before the usual window. Never before
    ``floor`` (the earliest activation).
    """

    candidates = [start]
    for month in settlements.months():
        begin = first_day(month)
        if begin < start <= _month_end(month):
            candidates.append(begin)
    for month in changed:
        begin = first_day(month)
        if begin <= end:
            candidates.append(begin)
    result = min(candidates)
    if floor is not None:
        result = max(result, floor)
    return result


def _month_end(month: Month) -> date:
    year, m = month
    nxt = date(year + (m == 12), m % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def diagnostics(
    factors: Mapping[Key, MonthFactor], settlements: Settlements
) -> dict[str, object]:
    """Value-free summary: state and factor per community, direction, month."""

    months: dict[str, dict[str, object]] = {}
    for (slug, direction, (year, month)), entry in sorted(
        factors.items(), key=lambda kv: (kv[0][0], kv[0][1].value, kv[0][2])
    ):
        months[f"{slug}/{direction.value}/{year:04d}-{month:02d}"] = {
            "state": str(entry.state),
            "factor": round(entry.factor, 4) if entry.factor is not None else None,
        }
    return {
        "months": months,
        "unknown_rows": settlements.unknown_rows,
        "ignored_rows": settlements.ignored_rows,
        "unmatched": sorted(
            f"{slug}/{year:04d}-{month:02d}"
            for slug, (year, month) in settlements.unmatched
        ),
        "other_points": sorted(
            f"{slug}/{direction.value}/{year:04d}-{month:02d}"
            for slug, direction, (year, month) in settlements.shared
        ),
    }


def settled_months(
    settlements: Settlements,
    factors: Mapping[Key, MonthFactor],
    previous: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, list[str]]:
    """Settled months per ``"<slug>/<direction>"`` (issue #75), value-free.

    A month counts as settled when it has a settlement, no unmatched
    correction, and this run didn't find it without provisional amounts to
    scale (``zero_provisional``: its money stays provisional). A month whose
    invoice also covers unselected metering points (#104) counts only once
    it was scaled by the member's total (``settled_shared``, #115): in this
    run, or, outside this run's window, in an earlier one (``previous``, the
    stored result). The dashboard and the € summary sensors use it for the
    settled / provisional marker.
    """

    before = {k: set(v) for k, v in (previous or {}).items()}
    out: dict[str, set[str]] = defaultdict(set)
    for key in settlements.amounts:
        slug, direction, (year, month) = key
        if (slug, (year, month)) in settlements.unmatched:
            continue
        name, label = f"{slug}/{direction.value}", f"{year:04d}-{month:02d}"
        factor = factors.get(key)
        if key in settlements.shared:
            if factor is None:
                if label in before.get(name, set()):
                    out[name].add(label)
                continue
            if factor.state is not MonthState.SETTLED_SHARED:
                continue
        elif factor is not None and factor.state is not MonthState.SETTLED:
            continue
        out[name].add(label)
    return {k: sorted(v) for k, v in sorted(out.items())}
