from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .audit_schemas import AuditEvent, ReviewerIdentity
from .workbench_schemas import (
    CaseDetail, CaseVersionDetail, RuleDispositionRecord,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaseNotFound(LookupError):
    pass


class CaseConflict(ValueError):
    pass


class CaseStore:
    """One persistent connection, serialized transactions, and no document logging."""

    def __init__(self, db_path: str | Path = ":memory:"):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(str(db_path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT,
                current_version INTEGER, status TEXT, assigned_office TEXT, assigned_reviewer TEXT
            );
            CREATE TABLE IF NOT EXISTS case_versions (
                case_id TEXT, version_number INTEGER, created_at TEXT, change_note TEXT,
                document TEXT, document_sha256 TEXT, context_json TEXT, decision_json TEXT,
                evidence_spans_json TEXT, context_warnings_json TEXT,
                PRIMARY KEY (case_id, version_number),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );
            CREATE TABLE IF NOT EXISTS rule_dispositions (
                case_id TEXT, version_number INTEGER, rule_id TEXT, office TEXT, disposition TEXT,
                reviewer TEXT, rationale TEXT, updated_at TEXT, carried_from_version INTEGER,
                invalidation_note TEXT, PRIMARY KEY (case_id, version_number, rule_id),
                FOREIGN KEY (case_id, version_number) REFERENCES case_versions(case_id, version_number)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY, case_id TEXT, version_number INTEGER,
                event_type TEXT, actor TEXT, payload_json TEXT, created_at TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS audit_events_case_time
                ON audit_events(case_id, created_at, event_id);
        """)

    @contextmanager
    def transaction(self):
        with self._lock:
            # BEGIN IMMEDIATE also serializes writers using other store instances.
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def get_case(self, case_id: str, version: int | None = None) -> CaseDetail:
        with self._lock:
            row = self.connection.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
            if row is None:
                raise CaseNotFound("Case not found")
            version = row["current_version"] if version is None else version
            v = self.connection.execute(
                "SELECT * FROM case_versions WHERE case_id = ? AND version_number = ?", (case_id, version),
            ).fetchone()
            if v is None:
                raise CaseNotFound("Case version not found")
            dispositions = self.connection.execute(
                "SELECT * FROM rule_dispositions WHERE case_id = ? AND version_number = ?", (case_id, version),
            ).fetchall()
            active = CaseVersionDetail(
                **{k: v[k] for k in ("version_number", "created_at", "change_note", "document", "document_sha256")},
                context=json.loads(v["context_json"]), decision=json.loads(v["decision_json"]),
                evidence_spans=json.loads(v["evidence_spans_json"]),
                context_warnings=json.loads(v["context_warnings_json"]),
                rule_dispositions={d["rule_id"]: RuleDispositionRecord(**{
                    k: d[k] for k in RuleDispositionRecord.model_fields
                }) for d in dispositions},
            )
            history = self.connection.execute(
                "SELECT version_number, created_at, change_note, document_sha256 FROM case_versions "
                "WHERE case_id = ? ORDER BY version_number", (case_id,),
            ).fetchall()
            decision = active.decision
            return CaseDetail(
                **dict(row), machine_verdict=decision.verdict, decided_by=decision.decided_by,
                priority=decision.priority, risk_index=decision.risk_index, offices=decision.offices,
                review_offices=decision.review_offices, active_version=active,
                version_history=[dict(h) for h in history],
            )

    def case_ids(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self.connection.execute("SELECT case_id FROM cases")]

    def insert_version(self, case_id: str, version: CaseVersionDetail) -> None:
        """Called inside a transaction together with the case head and audit update."""
        self.connection.execute(
            "INSERT INTO case_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, version.version_number, version.created_at, version.change_note,
             version.document, version.document_sha256, version.context.model_dump_json(),
             version.decision.model_dump_json(),
             json.dumps([s.model_dump() for s in version.evidence_spans]), json.dumps(version.context_warnings)),
        )
        for record in version.rule_dispositions.values():
            self.save_disposition(case_id, version.version_number, record)

    def save_disposition(self, case_id: str, version: int, record: RuleDispositionRecord) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO rule_dispositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, version, record.rule_id, record.office, record.disposition.value,
             record.reviewer, record.rationale, record.updated_at, record.carried_from_version,
             record.invalidation_note),
        )

    def audit(self, case_id: str, version: int, event_type: str, actor: str, payload: dict) -> None:
        self.connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, case_id, version, event_type, actor, json.dumps(payload), utc_now()),
        )

    def list_audit_events(self, case_id: str, *, version: int | None = None,
                         limit: int = 100, offset: int = 0) -> list[AuditEvent]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Audit pagination requires limit 1..200 and offset >= 0")
        with self._lock:
            if self.connection.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone() is None:
                raise CaseNotFound("Case not found")
            if version is not None and self.connection.execute(
                "SELECT 1 FROM case_versions WHERE case_id = ? AND version_number = ?", (case_id, version),
            ).fetchone() is None:
                raise CaseNotFound("Case version not found")
            query = "SELECT * FROM audit_events WHERE case_id = ?"
            params: list[object] = [case_id]
            if version is not None:
                query += " AND version_number = ?"
                params.append(version)
            query += " ORDER BY created_at, event_id LIMIT ? OFFSET ?"
            rows = self.connection.execute(query, [*params, limit, offset]).fetchall()
            return [AuditEvent(**{k: row[k] for k in AuditEvent.model_fields if k != "payload"},
                               payload=json.loads(row["payload_json"])) for row in rows]

    def list_reviewers(self) -> list[ReviewerIdentity]:
        """Observed reviewer names, retained in audit history even after a disposition changes."""
        with self._lock:
            rows = self.connection.execute(
                "SELECT actor, payload_json, created_at FROM audit_events "
                "WHERE event_type IN ('RULE_DISPOSITION_UPDATED', 'CASE_UPDATED') "
                "ORDER BY created_at, event_id",
            )
            identities: dict[tuple[str, str | None], ReviewerIdentity] = {}
            for row in rows:
                reviewer = row["actor"].strip()
                if not reviewer:
                    continue
                payload = json.loads(row["payload_json"])
                # assigned_office describes the assignee, not the acting reviewer's office.
                office = payload.get("office")
                identities[reviewer, office] = ReviewerIdentity(
                    reviewer=reviewer, office=office, last_seen_at=row["created_at"],
                )
            return sorted(identities.values(), key=lambda r: (r.reviewer.casefold(), r.office or ""))
