"""Append-only registry of the structure model (ADR 0053), from darkpool-analysis migrations
005/006 and `register_stress_parameter_set.py`: parameter sets, forecasts, research scores,
post-hoc validations, walk-forward evaluations and shadow audits.

Rows are never changed or deleted (SQLite triggers refuse it). Every row keeps the SHA-256
of its document and is checked again when read. Nothing here approves anything by itself:
a parameter set is approved only when a person registers an approved document that points
at an eligible evaluation (and, for production, an eligible shadow audit).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from typing import Any

from services.screening.structure.model import (
    APPROVED_STATUSES,
    forecast_sha256,
    parameter_sha256,
)
from services.screening.structure.sessions import canonical_sha256

STATUSES = frozenset({"RESEARCH_ONLY", "SHADOW_APPROVED", "PRODUCTION_APPROVED", "RETIRED"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")

_TABLES = (
    "parameter_sets",
    "forecasts",
    "research_scores",
    "validations",
    "evaluations",
    "shadow_audits",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS parameter_sets (
    parameter_set_id TEXT PRIMARY KEY,
    approval_status TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    approved_at TEXT,
    created_at_utc TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
    forecast_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    decision_timestamp_utc TEXT NOT NULL,
    forecast_status TEXT NOT NULL,
    parameter_set_id TEXT NOT NULL REFERENCES parameter_sets(parameter_set_id),
    sha256 TEXT NOT NULL UNIQUE,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS forecasts_by_parameters
    ON forecasts (parameter_set_id, decision_timestamp_utc);
CREATE TABLE IF NOT EXISTS research_scores (
    forecast_id TEXT PRIMARY KEY REFERENCES forecasts(forecast_id),
    sha256 TEXT NOT NULL UNIQUE,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validations (
    validation_id TEXT PRIMARY KEY,
    forecast_id TEXT NOT NULL REFERENCES forecasts(forecast_id),
    status TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    parameter_set_id TEXT REFERENCES parameter_sets(parameter_set_id),
    sha256 TEXT NOT NULL UNIQUE,
    eligible_for_shadow_review INTEGER NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_audits (
    audit_id TEXT PRIMARY KEY,
    parameter_set_id TEXT REFERENCES parameter_sets(parameter_set_id),
    sha256 TEXT NOT NULL UNIQUE,
    eligible_for_production_review INTEGER NOT NULL,
    document TEXT NOT NULL
);
""" + "".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END;
"""
    for table in _TABLES
)


class RegistryError(ValueError):
    pass


def validate_parameters(document: dict[str, Any]) -> None:
    """The original `validate()`, unchanged in meaning."""
    required = {
        "parameter_set_id",
        "approval_status",
        "feature_version",
        "minimum_tick",
        "minimum_complete_sessions",
        "minimum_coverage_ratio",
        "half_life_sessions",
        "zone_density_percentile",
        "resolution_overlap_threshold",
        "horizon_sessions",
        "coefficients",
    }
    missing = sorted(required - set(document))
    if missing:
        raise RegistryError(f"missing parameter fields: {','.join(missing)}")
    if document["approval_status"] not in STATUSES:
        raise RegistryError("invalid approval_status")
    for name in (
        "minimum_tick",
        "minimum_complete_sessions",
        "half_life_sessions",
        "horizon_sessions",
    ):
        if Decimal(str(document[name])) <= 0:
            raise RegistryError(f"{name} must be positive")
    for name in (
        "minimum_coverage_ratio",
        "zone_density_percentile",
        "resolution_overlap_threshold",
    ):
        if not 0 < Decimal(str(document[name])) <= 1:
            raise RegistryError(f"{name} must be in (0,1]")
    coefficients = document["coefficients"]
    if not isinstance(coefficients, dict) or set(coefficients) != {"up", "down"}:
        raise RegistryError("directional coefficients are required")
    if any(not isinstance(coefficients[d], dict) for d in ("up", "down")):
        raise RegistryError("directional coefficients are required")
    status = document["approval_status"]
    if status in APPROVED_STATUSES:
        fields = (
            "training_window",
            "validation_window",
            "eligible_universe",
            "selection_metric",
            "approved_at",
            "band_cut_points",
            "calibration_report_sha256",
        )
        absent = [name for name in fields if not document.get(name)]
        if absent:
            raise RegistryError(
                f"approved parameter set lacks validation metadata: {','.join(absent)}"
            )
        cuts = document["band_cut_points"]
        if set(cuts) != {"p50", "p75", "p90"}:
            raise RegistryError("invalid band_cut_points")
        p50, p75, p90 = (Decimal(str(cuts[key])) for key in ("p50", "p75", "p90"))
        if not 0 <= p50 < p75 < p90 <= 100:
            raise RegistryError("invalid band_cut_points")
        if not SHA256.fullmatch(str(document["calibration_report_sha256"])):
            raise RegistryError("invalid calibration_report_sha256")
        approved_at = datetime.fromisoformat(str(document["approved_at"]).replace("Z", "+00:00"))
        if approved_at.tzinfo is None:
            raise RegistryError("approved_at must include an offset")
    if status == "PRODUCTION_APPROVED" and not SHA256.fullmatch(
        str(document.get("shadow_audit_sha256") or "")
    ):
        raise RegistryError("PRODUCTION_APPROVED requires shadow_audit_sha256")


class Registry:
    def __init__(self, connection: sqlite3.Connection, lock: threading.Lock) -> None:
        self._db = connection
        self._lock = lock
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute("PRAGMA foreign_keys = ON")

    def _one(self, sql: str, args: tuple[Any, ...]) -> tuple[Any, ...] | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return None if row is None else tuple(row)

    def _insert(self, sql: str, args: tuple[Any, ...]) -> None:
        with self._lock:
            try:
                self._db.execute(sql, args)
            except sqlite3.IntegrityError as exc:
                raise RegistryError(str(exc)) from exc

    @staticmethod
    def _dump(document: dict[str, Any]) -> str:
        return json.dumps(document, ensure_ascii=False, sort_keys=True)

    # Parameter sets -------------------------------------------------------------------

    def register_parameters(self, document: dict[str, Any], now: str) -> str:
        validate_parameters(document)
        status = document["approval_status"]
        if status in APPROVED_STATUSES:
            row = self._one(
                "SELECT eligible_for_shadow_review FROM evaluations WHERE sha256 = ?",
                (document["calibration_report_sha256"],),
            )
            if row is None or row[0] != 1:
                raise RegistryError(
                    "calibration report is absent or not eligible for shadow review"
                )
        if status == "PRODUCTION_APPROVED":
            row = self._one(
                "SELECT eligible_for_production_review FROM shadow_audits WHERE sha256 = ?",
                (document["shadow_audit_sha256"],),
            )
            if row is None or row[0] != 1:
                raise RegistryError("shadow audit is absent or not eligible for production review")
        digest = parameter_sha256(document)
        self._insert(
            "INSERT INTO parameter_sets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                document["parameter_set_id"],
                status,
                document["feature_version"],
                digest,
                document.get("approved_at"),
                now,
                self._dump(document),
            ),
        )
        return digest

    def parameters(self, parameter_set_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT sha256, document FROM parameter_sets WHERE parameter_set_id = ?",
            (parameter_set_id,),
        )
        if row is None:
            return None
        document = dict(json.loads(row[1]))
        if parameter_sha256(document) != row[0]:
            raise RegistryError("parameter set hash mismatch")
        return document

    def operational_parameters(self) -> dict[str, Any] | None:
        """The newest production-approved set, else the newest shadow-approved one."""
        for status in ("PRODUCTION_APPROVED", "SHADOW_APPROVED"):
            row = self._one(
                "SELECT parameter_set_id FROM parameter_sets WHERE approval_status = ? "
                "ORDER BY approved_at DESC, created_at_utc DESC LIMIT 1",
                (status,),
            )
            if row is not None:
                return self.parameters(str(row[0]))
        return None

    # Forecasts ------------------------------------------------------------------------

    def add_forecast(self, forecast: dict[str, Any], research: dict[str, Any] | None) -> None:
        if forecast_sha256(forecast) != forecast["forecast_sha256"]:
            raise RegistryError("forecast hash mismatch")
        stored = self.parameters(str(forecast["parameter_set_id"]))
        if stored is None or parameter_sha256(stored) != forecast["parameter_set_sha256"]:
            raise RegistryError("forecast parameter set is not the registered one")
        self._insert(
            "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                forecast["forecast_id"],
                forecast["symbol"],
                forecast["decision_timestamp_utc"],
                forecast["forecast_status"],
                forecast["parameter_set_id"],
                forecast["forecast_sha256"],
                self._dump(forecast),
            ),
        )
        if research is not None:
            self._insert(
                "INSERT INTO research_scores VALUES (?, ?, ?)",
                (research["forecast_id"], research["research_sha256"], self._dump(research)),
            )

    def forecast(self, forecast_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT sha256, document FROM forecasts WHERE forecast_id = ?", (forecast_id,)
        )
        if row is None:
            return None
        document = dict(json.loads(row[1]))
        if document.get("forecast_sha256") != row[0] or forecast_sha256(document) != row[0]:
            raise RegistryError("stored forecast hash mismatch")
        return document

    def research(self, forecast_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT sha256, document FROM research_scores WHERE forecast_id = ?", (forecast_id,)
        )
        if row is None:
            return None
        document = dict(json.loads(row[1]))
        body = {k: v for k, v in document.items() if k != "research_sha256"}
        if canonical_sha256(body) != row[0]:
            raise RegistryError("stored research hash mismatch")
        return document

    def forecasts_for(self, parameter_set_id: str) -> list[dict[str, Any]]:
        with self._lock:
            ids = [
                str(row[0])
                for row in self._db.execute(
                    "SELECT forecast_id FROM forecasts WHERE parameter_set_id = ? "
                    "ORDER BY decision_timestamp_utc, forecast_id",
                    (parameter_set_id,),
                ).fetchall()
            ]
        return [doc for doc in (self.forecast(i) for i in ids) if doc is not None]

    def unvalidated(self) -> list[dict[str, Any]]:
        """Forecasts with scores (issued or research) and no validation yet."""
        with self._lock:
            ids = [
                str(row[0])
                for row in self._db.execute(
                    "SELECT f.forecast_id FROM forecasts f LEFT JOIN research_scores r "
                    "USING (forecast_id) LEFT JOIN validations v USING (forecast_id) "
                    "WHERE v.validation_id IS NULL AND (f.forecast_status = 'ISSUED' OR "
                    "r.forecast_id IS NOT NULL) ORDER BY f.decision_timestamp_utc"
                ).fetchall()
            ]
        return [doc for doc in (self.forecast(i) for i in ids) if doc is not None]

    # Validations, evaluations, audits -------------------------------------------------

    def add_validation(self, validation: dict[str, Any]) -> None:
        self._insert(
            "INSERT INTO validations VALUES (?, ?, ?, ?, ?)",
            (
                validation["validation_id"],
                validation["forecast_id"],
                validation["status"],
                validation["validation_sha256"],
                self._dump(validation),
            ),
        )

    def validations_for(self, parameter_set_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT f.forecast_id, v.sha256, v.document FROM forecasts f JOIN validations v "
                "USING (forecast_id) WHERE f.parameter_set_id = ? ORDER BY "
                "f.decision_timestamp_utc, v.validation_id",
                (parameter_set_id,),
            ).fetchall()
        pairs = []
        for forecast_id, digest, body in rows:
            validation = dict(json.loads(body))
            check = {k: v for k, v in validation.items() if k != "validation_sha256"}
            if canonical_sha256(check) != digest:
                raise RegistryError("stored validation hash mismatch")
            forecast = self.forecast(str(forecast_id))
            if forecast is not None:
                pairs.append((forecast, validation))
        return pairs

    def add_evaluation(
        self, evaluation_id: str, parameter_set_id: str | None, report: dict[str, Any]
    ) -> None:
        self._insert(
            "INSERT INTO evaluations VALUES (?, ?, ?, ?, ?)",
            (
                evaluation_id,
                parameter_set_id,
                report["report_sha256"],
                1 if report["eligible_for_shadow_review"] else 0,
                self._dump(report),
            ),
        )

    def add_shadow_audit(
        self, audit_id: str, parameter_set_id: str | None, report: dict[str, Any]
    ) -> None:
        self._insert(
            "INSERT INTO shadow_audits VALUES (?, ?, ?, ?, ?)",
            (
                audit_id,
                parameter_set_id,
                report["report_sha256"],
                1 if report["eligible_for_production_review"] else 0,
                self._dump(report),
            ),
        )
