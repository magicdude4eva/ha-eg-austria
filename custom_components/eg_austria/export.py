"""Opt-in redacted debug export (issue #84, ADR 0016).

Started by the user with the ``eg_austria.debug_export`` action. It sends the
same GET requests a run sends (ADR 0002 whitelist, request spacing, under the
run lock) for a short recent window and writes the responses, as the portal
sent them, to one JSON file under the configuration directory:

* ``portal-session``, ``metering-point``, ``version``, ``maintenance``;
* ``day-completeness`` for the window;
* ``analysis-data`` per selected community (Day and Hour, all selected
  metering points) and per community and metering point (Day), so each
  direction can be seen on its own.

Unlike diagnostics, energy and money values are kept: the file is meant to
be loaded as test fixtures to reproduce a parsing or validation failure.
Ids are replaced by stable synthetic values (member ``9000001``, location
``5001``, metering points ``1001``..., communities ``2001``...; metering
point numbers ``AT0...0<n>``) consistently across all responses, so the
file still parses. Names, addresses, emails, tokens and every other
``TO_REDACT`` key are removed, and every remaining string is scrubbed with
``redact_text()`` against the original ids.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import DOMAIN, VERSION
from .coordinator import MAINTENANCE_MARGIN
from .exceptions import AuthError, EgAustriaError, SchemaError
from .importer import local_today
from .models import (
    _DAY_FLAGS,
    _TOTALS,
    Aggregation,
    Direction,
    MeteringPointInfo,
    parse_metering_points,
)
from .redact import REDACTED, TO_REDACT, redact_text, schema_fingerprint
from .snapshot import setup_shape, slug_map

if TYPE_CHECKING:
    from .coordinator import EgAustriaCoordinator

EXPORT_VERSION: Final = 1
DEFAULT_DAYS: Final = 7
MAX_DAYS: Final = 31
NOTICE: Final = (
    "EG Austria debug export. Contains energy and money values of the "
    "selected metering points. Ids are replaced by synthetic values; names, "
    "addresses, emails and tokens are removed. Check before sharing."
)

_BASES: Final = {"member": 9000001, "location": 5001, "mp": 1001, "community": 2001}
# Keys whose value is an id of the given kind (single value or list).
_KEY_KIND: Final = {
    "memberId": "member",
    "locationId": "location",
    "energyCommunityId": "community",
    "communityId": "community",
    "energyCommunityIds": "community",
    "desiredPartFactorParticipatingCommunityIds": "community",
    "meteringPointId": "mp",
    "mpIds": "mp",
}
_MP_LIST: Final = "memberMeteringpoints"
_PLAN_NUMBERS: Final = frozenset(("fromMeteringpointNumber", "toMeteringpointNumber"))
_ENDPOINTS: Final = {
    "/v1/portal/portal-session": "portal-session",
    "/v1/portal/member/{member}/metering-point": "metering-point",
    "/v1/version": "version",
    "/v1/portal/maintenance": "maintenance",
    "/v1/portal/member/{member}/analysis-data/day-completeness": "day-completeness",
    "/v1/portal/member/{member}/analysis-data": "analysis-data",
}


@dataclass(frozen=True, slots=True)
class _Keep:
    """A synthetic value that must survive key redaction and scrubbing."""

    value: Any


@dataclass(slots=True)
class Pseudonyms:
    """Stable original -> synthetic id mapping for one export."""

    _maps: dict[str, dict[str, int]] = field(default_factory=dict)
    _numbers: dict[str, str] = field(default_factory=dict)

    def get(self, kind: str, value: Any) -> Any:
        """Return the synthetic value; str stays str, int stays int."""

        if value is None or isinstance(value, bool):
            return value
        mapping = self._maps.setdefault(kind, {})
        key = str(value)
        if key not in mapping:
            mapping[key] = _BASES[kind] + len(mapping)
        synthetic = mapping[key]
        return str(synthetic) if isinstance(value, str) else synthetic

    def number(self, value: Any) -> Any:
        """Return a synthetic 33-character metering point number."""

        if not isinstance(value, str):
            return value
        if value not in self._numbers:
            self._numbers[value] = "AT" + str(len(self._numbers) + 1).zfill(31)
        return self._numbers[value]

    def originals(self) -> list[str]:
        """Every original value, for ``redact_text()``."""

        return [k for m in self._maps.values() for k in m] + list(self._numbers)


def _pseudonymize(value: Any, p: Pseudonyms, parent: str | None = None) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            kind = _KEY_KIND.get(k)
            if k == "id" and parent == _MP_LIST:
                kind = "mp"
            if kind is not None and not isinstance(v, Mapping):
                if isinstance(v, list):
                    out[k] = _Keep([p.get(kind, i) for i in v])
                else:
                    out[k] = _Keep(p.get(kind, v))
            elif k == "number" and parent == _MP_LIST:
                out[k] = _Keep(p.number(v))
            elif k in _PLAN_NUMBERS:
                # Zero plans (issue #89): same synthetic numbers as the
                # metering points, so own-site plans stay recognisable.
                out[k] = _Keep(p.number(v))
            else:
                out[k] = _pseudonymize(v, p, k)
        return out
    if isinstance(value, list):
        return [_pseudonymize(v, p, parent) for v in value]
    return value


def _redact(value: Any, sensitive: list[str]) -> Any:
    if isinstance(value, _Keep):
        return value.value
    if isinstance(value, Mapping):
        return {
            redact_text(str(k), sensitive): (
                REDACTED
                if k in TO_REDACT and not isinstance(v, _Keep)
                else _redact(v, sensitive)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, sensitive) for v in value]
    if isinstance(value, str):
        return redact_text(value, sensitive)
    return value


def redact_export(value: Any, p: Pseudonyms, sensitive: Iterable[object]) -> Any:
    """Replace ids by synthetic values, drop ``TO_REDACT`` keys, scrub strings."""

    marked = _pseudonymize(value, p)
    originals = [str(s) for s in sensitive if s not in (None, "")] + p.originals()
    # Longest first, so a short id can't cut into a longer one.
    return _redact(marked, sorted(set(originals), key=len, reverse=True))


def key_presence(raw: Any) -> dict[str, Any] | None:
    """Per direction: point count and which totals / day flags are missing.

    Answers issue #78 type questions straight from the export: did the
    portal leave out the totals of a direction, and did it send points?
    """

    if not isinstance(raw, Mapping):
        return None
    result: dict[str, Any] = {}
    for direction, list_key in (
        (Direction.CONSUMPTION, "consumptionDataPoints"),
        (Direction.GENERATION, "generationDataPoints"),
    ):
        word = "generation" if direction is Direction.GENERATION else "consumption"
        points = raw.get(list_key)
        result[direction.value] = {
            "points": len(points) if isinstance(points, list) else None,
            "missing": sorted(
                k for k in (*_TOTALS, *_DAY_FLAGS) if word in k.lower() and k not in raw
            ),
        }
    return result


@dataclass(slots=True)
class _Recorder:
    """Collects every raw response the client receives (``raw_sink``)."""

    records: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, template: str, params: list[tuple[str, str]], raw: Any) -> None:
        self.records.append(
            {
                "endpoint": _ENDPOINTS.get(template, template),
                "params": [list(p) for p in params],
                "raw": raw,
            }
        )


async def async_collect(
    coordinator: EgAustriaCoordinator, days: int = DEFAULT_DAYS
) -> dict[str, Any]:
    """Send the export's requests and return the redacted document."""

    setup = coordinator.entry_setup()
    client = coordinator.client
    start, end = window(local_today(dt_util.utcnow()), days)
    recorder = _Recorder()
    steps: list[dict[str, Any]] = []
    info: MeteringPointInfo | None = None

    async def step(name: str, call: Callable[[], Awaitable[Any]]) -> Any:
        before = len(recorder.records)
        entry: dict[str, Any] = {"step": name, "result": "ok"}
        result = None
        try:
            result = await call()
        except AuthError:
            raise
        except SchemaError as err:
            entry["result"] = f"schema: {err}"
        except EgAustriaError as err:
            entry["result"] = f"error: {type(err).__name__}: {err}"
        entry["responses"] = list(range(before, len(recorder.records)))
        steps.append(entry)
        return result

    async with coordinator.run_lock:
        client.raw_sink = recorder
        try:
            await step("version", client.get_version)
            windows = await step("maintenance", client.get_maintenance) or []
            now, margin = dt_util.utcnow(), MAINTENANCE_MARGIN.total_seconds()
            if any(w.active_at(now, margin) for w in windows):
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="export_maintenance"
                )
            await step("portal-session", client.get_session)
            info = await step(
                "metering-point", lambda: client.get_metering_points(setup.member)
            )
            ids, mps = setup.community_ids, list(setup.mp_ids)
            await step(
                "day-completeness",
                lambda: client.get_day_completeness(setup.member, start, end, ids, mps),
            )
            for community in setup.communities:
                for aggregation in (Aggregation.DAY, Aggregation.HOUR):
                    await step(
                        f"analysis-data {community.slug} {aggregation.value}",
                        partial(
                            client.get_analysis,
                            setup.member,
                            aggregation,
                            start,
                            end,
                            [community.id],
                            mps,
                        ),
                    )
                for index, mp in enumerate(mps, 1):
                    await step(
                        f"analysis-data {community.slug} Day mp{index}",
                        partial(
                            client.get_analysis,
                            setup.member,
                            Aggregation.DAY,
                            start,
                            end,
                            [community.id],
                            [mp],
                        ),
                    )
        finally:
            client.raw_sink = None

    p = Pseudonyms()
    # Fixed order: the entry's own ids get the first synthetic values.
    p.get("member", setup.member)
    p.get("location", setup.location_id)
    for mp in setup.mp_ids:
        p.get("mp", mp)
    for community in setup.communities:
        p.get("community", community.id)
    sensitive = setup.sensitive()
    responses = []
    for record in recorder.records:
        params = [
            [k, str(p.get(_KEY_KIND[k], int(v)))] if k in _KEY_KIND else [k, v]
            for k, v in record["params"]
        ]
        raw = record["raw"]
        item: dict[str, Any] = {
            "endpoint": record["endpoint"],
            "params": params,
            "schema_fingerprint": schema_fingerprint(raw),
            "raw": redact_export(raw, p, sensitive),
        }
        if record["endpoint"] == "analysis-data":
            item["key_presence"] = key_presence(raw)
        responses.append(item)
    if info is None and any(r["endpoint"] == "metering-point" for r in responses):
        try:
            info = parse_metering_points(
                next(
                    r["raw"]
                    for r in recorder.records
                    if r["endpoint"] == "metering-point"
                )
            )
        except SchemaError:
            info = None
    entry = coordinator.config_entry
    return {
        "notice": NOTICE,
        "export_version": EXPORT_VERSION,
        "created": dt_util.utcnow().isoformat(),
        "integration_version": VERSION,
        "home_assistant_version": HA_VERSION,
        "window": [start.isoformat(), end.isoformat()],
        "entry": {
            "version": f"{entry.version}.{entry.minor_version}",
            "options": dict(entry.options),
            "site_slug": setup.site_slug,
            "member": p.get("member", setup.member),
            "location": p.get("location", setup.location_id),
            "metering_points": [p.get("mp", m) for m in setup.mp_ids],
            "communities": [
                {"slug": c.slug, "id": p.get("community", c.id)}
                for c in setup.communities
            ],
        },
        "setup": (
            setup_shape(
                info, setup.location_id, setup.mp_ids, slug_map(setup.communities)
            )
            if info is not None
            else None
        ),
        "steps": redact_export(steps, p, sensitive),
        "responses": responses,
    }


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n")


async def async_write_export(
    hass: HomeAssistant, coordinator: EgAustriaCoordinator, days: int = DEFAULT_DAYS
) -> dict[str, Any]:
    """Collect and write the export; return path and counts for the action."""

    document = await async_collect(coordinator, days)
    stamp = dt_util.utcnow().strftime("%Y%m%dT%H%M%SZ")
    slug = document["entry"]["site_slug"]
    path = Path(hass.config.path(f"eg_austria_debug_{slug}_{stamp}.json"))
    await hass.async_add_executor_job(_write, path, document)
    errors = sum(1 for s in document["steps"] if s["result"] != "ok")
    return {
        "path": str(path),
        "responses": len(document["responses"]),
        "failed_steps": errors,
    }


def window(today: date, days: int) -> tuple[date, date]:
    """Return the export window ending today (used by tests and the docs)."""

    days = max(1, min(int(days), MAX_DAYS))
    return today - timedelta(days=days - 1), today
