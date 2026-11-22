"""Value-free snapshots kept in the store for diagnostics (issue #78).

Diagnostics taken after a restart used to lose everything the coordinator
only held in memory: the last run, the directions of the selected metering
points and the response shapes. The coordinator now stores the snapshots
built here after every run, so a user can send diagnostics at any time and
we can still see how parsing could fail.

Nothing here contains an id, a name, a metering point number or an energy
or money value: metering points are listed by position, communities by
their slug (or ``other``), activations as months, responses as
``schema_fingerprint()`` shapes (key -> type name).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from .models import MeteringPointInfo

if TYPE_CHECKING:
    from .coordinator import RunSummary

OTHER_COMMUNITY = "other"


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _month(value: date | None) -> str | None:
    return value.strftime("%Y-%m") if value is not None else None


def setup_shape(
    info: MeteringPointInfo,
    location_id: str,
    mp_ids: Collection[int],
    community_slugs: Mapping[int, str],
) -> dict[str, Any]:
    """Describe the selected metering points without ids or names.

    ``community_slugs`` maps the selected community ids to their slugs; every
    other community shows as ``other``. Activations are given as months.
    """

    location = info.location(location_id)
    points = location.metering_points if location is not None else ()
    selected = [mp for mp in points if mp.id in mp_ids]
    by_direction: dict[str, int] = {}
    for mp in points:
        by_direction[mp.direction.value] = by_direction.get(mp.direction.value, 0) + 1
    return {
        "locations": len(info.locations),
        "location_found": location is not None,
        "location_points": dict(sorted(by_direction.items())),
        "selected_found": len(selected),
        "selected_missing": len(set(mp_ids) - {mp.id for mp in selected}),
        "selected": [
            {
                "direction": mp.direction.value,
                "activations": [
                    {
                        "community": community_slugs.get(
                            act.community_id, OTHER_COMMUNITY
                        ),
                        "type": act.community_type,
                        "status": act.status,
                        "from": _month(act.active_from),
                        "to": _month(act.active_to),
                    }
                    for act in sorted(
                        mp.activations, key=lambda a: (a.active_from, a.community_type)
                    )
                ],
            }
            for mp in sorted(selected, key=lambda m: m.direction.value)
        ],
    }


def run_snapshot(summary: RunSummary | None) -> dict[str, Any] | None:
    """Return the ``last_run`` diagnostics section: dates, counts, rule ids."""

    if summary is None:
        return None
    validation = summary.validation
    return {
        "run_type": str(summary.run_type),
        "status": str(summary.status),
        "started": _iso(summary.started),
        "finished": _iso(summary.finished),
        "duration_s": round(summary.duration, 1),
        "window": [_iso(d) for d in summary.window] if summary.window else None,
        "chunks": summary.chunks,
        "requests": summary.requests,
        "portal_version_changed": summary.portal_version_changed,
        "rescheduled_for": _iso(summary.rescheduled_for),
        "stale_client": summary.stale_client,
        "error": summary.error,
        "issues": {
            "api_changed": summary.issues.api_changed,
            "implausible_data": summary.issues.implausible_data,
            "portal_unreachable": summary.issues.portal_unreachable,
        },
        "written_rows": dict(summary.written_rows),
        "kept_stored_hours": dict(summary.kept_stored_hours),
        "blocked": {k: [_iso(d) for d in v] for k, v in summary.blocked.items()},
        "validation": (
            {
                # Rule ids, statistic keys and dates only; messages may
                # contain values and are left out.
                "findings": [
                    {
                        "rule": f.rule,
                        "severity": str(f.severity),
                        "statistic": f.statistic,
                        "chunk": [_iso(d) for d in f.chunk] if f.chunk else None,
                        "day": _iso(f.day),
                    }
                    for f in validation.findings
                ],
                "schema_extra": sorted(validation.schema_extra),
                "corrections": [
                    {
                        "day": _iso(c.day),
                        "statistic": c.statistic,
                        "relative_change": c.relative_change,
                    }
                    for c in validation.corrections
                ],
            }
            if validation is not None
            else None
        ),
    }


def slug_map(communities: Iterable[Any]) -> dict[int, str]:
    """Return selected community id -> slug from ``EntrySetup.communities``."""

    return {c.id: c.slug for c in communities}
