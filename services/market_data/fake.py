from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from packages.contracts.validator import validate_document
from services.market_data.calendar import UsEquityCalendar
from services.market_data.provider import (
    BarsQuery,
    BarsResult,
    FixtureIntegrityError,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
)

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "market_data"
    / "fake_aapl_1m_r1.json"
)
MAX_QUERY_MINUTES = 1_440


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise FixtureIntegrityError("fixture timestamps must use UTC")
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeMarketDataProvider:
    """Deterministic, file-backed provider that never performs network I/O."""

    def __init__(
        self,
        document: dict[str, Any],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._calendar = UsEquityCalendar("XNAS")
        self._capabilities = self._object(document, "capabilities")
        bars_value = document.get("bars")
        if not isinstance(bars_value, list) or not bars_value:
            raise FixtureIntegrityError("fixture bars must be a non-empty array")

        try:
            validate_document(self._capabilities)
        except ValueError as exc:
            raise FixtureIntegrityError(f"invalid capabilities: {exc}") from exc

        bars: list[dict[str, Any]] = []
        identities: set[tuple[str, str, str, str]] = set()
        for value in bars_value:
            if not isinstance(value, dict):
                raise FixtureIntegrityError("each fixture bar must be an object")
            bar = cast(dict[str, Any], value)
            try:
                validate_document(bar)
            except ValueError as exc:
                raise FixtureIntegrityError(f"invalid price bar: {exc}") from exc
            identity = (
                cast(str, bar["instrument_id"]),
                cast(str, bar["timeframe"]),
                cast(str, bar["interval_start_utc"]),
                cast(str, bar["dataset_revision"]),
            )
            if identity in identities:
                raise FixtureIntegrityError("duplicate canonical bar identity")
            identities.add(identity)
            bars.append(deepcopy(bar))

        self._bars = tuple(sorted(bars, key=lambda bar: cast(str, bar["interval_start_utc"])))
        source_ids = {cast(str, bar["source_id"]) for bar in self._bars}
        if source_ids != {self.source_id}:
            raise FixtureIntegrityError("all bars must match the capability source_id")

    @classmethod
    def from_path(
        cls,
        path: Path = DEFAULT_FIXTURE_PATH,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> FakeMarketDataProvider:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise FixtureIntegrityError("fixture root must be an object")
        return cls(cast(dict[str, Any], value), now=now)

    @staticmethod
    def _object(document: dict[str, Any], key: str) -> dict[str, Any]:
        value = document.get(key)
        if not isinstance(value, dict):
            raise FixtureIntegrityError(f"fixture {key} must be an object")
        return cast(dict[str, Any], value)

    @property
    def source_id(self) -> str:
        return cast(str, self._capabilities["source_id"])

    def capabilities(self) -> dict[str, Any]:
        return deepcopy(self._capabilities)

    def get_bars(self, query: BarsQuery) -> BarsResult:
        self._validate_query(query)
        matching_instrument = [
            bar for bar in self._bars if bar["instrument_id"] == query.instrument_id
        ]
        if not matching_instrument:
            raise InstrumentNotFoundError("instrument is not available in the synthetic fixture")

        candidates = [
            bar
            for bar in matching_instrument
            if query.start_utc <= _parse_utc(cast(str, bar["interval_start_utc"])) < query.end_utc
        ]
        visible = [
            bar
            for bar in candidates
            if query.as_of_utc is None
            or _parse_utc(cast(str, bar["available_at_utc"])) <= query.as_of_utc
        ]

        expected_starts = tuple(
            start
            for offset in range(int((query.end_utc - query.start_utc).total_seconds() // 60))
            if self._calendar.classify_interval(
                (start := query.start_utc + timedelta(minutes=offset)),
                start + timedelta(minutes=1),
            )
            in {"pre", "regular", "post"}
        )
        visible_by_start = {
            _parse_utc(cast(str, bar["interval_start_utc"])): bar for bar in visible
        }
        candidate_starts = {_parse_utc(cast(str, bar["interval_start_utc"])) for bar in candidates}
        missing = []
        for start in expected_starts:
            if start in visible_by_start:
                continue
            reason = (
                "not_available_as_of_cutoff"
                if start in candidate_starts and query.as_of_utc is not None
                else "fixture_missing"
            )
            missing.append(
                {
                    "start_utc": _format_utc(start),
                    "end_utc": _format_utc(start + timedelta(minutes=1)),
                    "reason": reason,
                }
            )

        coverage_status = (
            "complete" if visible and not missing else ("partial" if visible else "empty")
        )
        exemplar = matching_instrument[0]
        revisions = {cast(str, bar["dataset_revision"]) for bar in matching_instrument}
        if len(revisions) != 1:
            raise FixtureIntegrityError("fake provider requires one pinned dataset revision")
        coverage: dict[str, Any] = {
            "kind": "coverage_manifest",
            "schema_version": "0.1",
            "instrument_id": query.instrument_id,
            "timeframe": query.timeframe,
            "requested_start_utc": _format_utc(query.start_utc),
            "requested_end_utc": _format_utc(query.end_utc),
            "coverage_status": coverage_status,
            "verification_status": "verified",
            "observed_interval_count": len(visible),
            "expected_interval_count": len(expected_starts),
            "missing_intervals": missing,
            "source_id": self.source_id,
            "source_feed_scope": exemplar["source_feed_scope"],
            "adjustment": exemplar["adjustment"],
            "dataset_revision": next(iter(revisions)),
            "point_in_time_cutoff_utc": (
                _format_utc(query.as_of_utc) if query.as_of_utc is not None else None
            ),
        }
        result = BarsResult(tuple(deepcopy(bar) for bar in visible), coverage)
        validate_document(result.to_document())
        return result

    def _validate_query(self, query: BarsQuery) -> None:
        if query.timeframe != "1m":
            raise InvalidBarsQueryError("fake provider supports only the 1m timeframe")
        for name, value in (("start_utc", query.start_utc), ("end_utc", query.end_utc)):
            if value.utcoffset() != timedelta(0):
                raise InvalidBarsQueryError(f"{name} must be UTC")
            if value.second or value.microsecond:
                raise InvalidBarsQueryError(f"{name} must align to a minute boundary")
        if query.end_utc <= query.start_utc:
            raise InvalidBarsQueryError("end_utc must be after start_utc")
        if (query.end_utc - query.start_utc) > timedelta(minutes=MAX_QUERY_MINUTES):
            raise InvalidBarsQueryError(
                f"fake provider range cannot exceed {MAX_QUERY_MINUTES} minutes"
            )
        if query.as_of_utc is not None:
            if query.as_of_utc.utcoffset() != timedelta(0):
                raise InvalidBarsQueryError("as_of_utc must be UTC")
            if query.as_of_utc > self._now():
                raise InvalidBarsQueryError("as_of_utc cannot be in the future")
