"""SQLite persistence for the local quality workbench."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal, overload

from src.knowledge_config import PRODUCT_ROOT, get_active_knowledge


DEFAULT_DB_PATH = PRODUCT_ROOT / "data" / "runtime" / "workbench.sqlite3"
ALLOWED_SOURCE_SUFFIXES = {".md", ".txt", ".pdf"}
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_ACTIVE_JOBS = 2
MAX_REVISIONS = 100
MAX_RUNTIME_SOURCE_BYTES = 500 * 1024 * 1024

# Maps a `classify_coverage_item` disposition to the initial ledger status
# (docs/session-reports/2026-08-01-coverage-loop-design.md: candidate ->
# auto_classified -> auto_approved/auto_rejected/auto_quarantined -> ... ->
# active). `add_candidate` lands on `auto_classified`, not `auto_approved`:
# promoting further also requires a before/after improvement check (design-doc
# step 6, not implemented yet) — auto-approving without it would be exactly
# the self-graded "it works" this repo's verification discipline forbids.
COVERAGE_DISPOSITION_TO_STATUS = {
    "add_candidate": "auto_classified",
    "rejected": "auto_rejected",
    "quarantined": "auto_quarantined",
    "no_gap": "no_gap",
}
# A quarantined candidate is the only state a human resolves by hand (design-
# doc: "隔離だけまとめてユーザー確認する"); resolution can only land on one of
# these two terminal auto_* states, not an arbitrary status.
COVERAGE_QUARANTINE_RESOLUTIONS = frozenset({"auto_approved", "auto_rejected"})
# Statuses a human may still resolve from. `auto_rejected` is here because it is
# reached by a judgment measured to be unreliable: on the 2026-08-02 run, A-answers
# with identical sourcing were split 9-to-7 between `invalid_A` (-> rejected) and
# `missing_knowledge` (-> candidate), and the same judge disagreed with itself across
# runs on 6 of 30 questions. A coin-flip must not write to a state nobody can reopen.
# `auto_approved` and `no_gap` stay closed: one is an accepted decision, the other
# means A and B agreed and there is nothing to reopen.
COVERAGE_RESOLVABLE_STATUSES = frozenset({"auto_quarantined", "auto_rejected"})


class WorkbenchConflictError(RuntimeError):
    """The requested state transition conflicts with persisted workbench state."""


class WorkbenchNotFoundError(LookupError):
    """A requested workbench record does not exist."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class WorkbenchStore:
    """Own the append-only revision history and mutable job lifecycle."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        product_root: str | Path = PRODUCT_ROOT,
        runtime_root: str | Path | None = None,
        bootstrap: bool = True,
        engine_fingerprint: str = "",
    ) -> None:
        self.product_root = Path(product_root).resolve()
        self.db_path = Path(db_path).resolve()
        self.runtime_root = Path(runtime_root or self.db_path.parent).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self._create_schema()
        self.recover_interrupted_jobs(stale_after_seconds=15 * 60)
        if bootstrap:
            self.bootstrap_active_revision(engine_fingerprint=engine_fingerprint)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT PRIMARY KEY,
                    parent_revision_id TEXT REFERENCES revisions(id),
                    label TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
                    config_json TEXT NOT NULL,
                    created_engine_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workbench_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    active_revision_id TEXT NOT NULL REFERENCES revisions(id),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    revision_id TEXT REFERENCES revisions(id),
                    job_id TEXT,
                    run_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    kind TEXT NOT NULL CHECK(kind IN ('comparison', 'validation')),
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'running', 'passed', 'failed', 'error', 'interrupted'
                    )),
                    engine_fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    job_id TEXT REFERENCES jobs(id),
                    question TEXT NOT NULL,
                    candidate_answer TEXT NOT NULL,
                    delivery_status TEXT NOT NULL CHECK(delivery_status IN ('released', 'blocked')),
                    blocked_reason TEXT,
                    verification_json TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    status TEXT NOT NULL CHECK(status IN ('disabled', 'flushed', 'error')),
                    trace_url TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS async_runs (
                    id TEXT PRIMARY KEY,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    question TEXT NOT NULL,
                    answer_mode TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
                    response_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS adjustments (
                    id TEXT PRIMARY KEY,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    run_id TEXT REFERENCES runs(id),
                    job_id TEXT REFERENCES jobs(id),
                    category TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS coverage_candidates (
                    id TEXT PRIMARY KEY,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    question TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT '',
                    disposition TEXT NOT NULL CHECK(disposition IN (
                        'add_candidate', 'rejected', 'quarantined', 'no_gap'
                    )),
                    failure_cause TEXT,
                    candidate_reason TEXT NOT NULL DEFAULT '',
                    external_answer_json TEXT NOT NULL,
                    knowledge_answer_json TEXT NOT NULL,
                    fact_check_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'no_gap', 'auto_classified', 'auto_approved', 'auto_rejected',
                        'auto_quarantined', 'implemented', 'verified', 'active'
                    )),
                    status_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_revision ON events(revision_id, id);
                CREATE INDEX IF NOT EXISTS idx_jobs_revision ON jobs(revision_id, kind, created_at);
                CREATE INDEX IF NOT EXISTS idx_runs_revision ON runs(revision_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_async_runs_status ON async_runs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_adjustments_created ON adjustments(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_coverage_candidates_revision
                    ON coverage_candidates(revision_id, status, created_at);

                CREATE TRIGGER IF NOT EXISTS revisions_no_update
                BEFORE UPDATE ON revisions BEGIN
                    SELECT RAISE(ABORT, 'revisions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS revisions_no_delete
                BEFORE DELETE ON revisions BEGIN
                    SELECT RAISE(ABORT, 'revisions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS events_no_update
                BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete
                BEFORE DELETE ON events BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS runs_no_update
                BEFORE UPDATE ON runs BEGIN
                    SELECT RAISE(ABORT, 'runs are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS runs_no_delete
                BEFORE DELETE ON runs BEGIN
                    SELECT RAISE(ABORT, 'runs are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS async_run_events_no_update
                BEFORE UPDATE ON events WHEN OLD.run_id IS NOT NULL BEGIN
                    SELECT RAISE(ABORT, 'run events are append-only');
                END;
                """
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _decode(value: str | None, fallback: Any = None) -> Any:
        return json.loads(value) if value else fallback

    def _append_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        *,
        revision_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(event_type, revision_id, job_id, run_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_type, revision_id, job_id, run_id, self._json(payload or {}), utc_now()),
        )

    def _resolve_source(self, source_path: str | Path) -> Path:
        candidate = Path(source_path)
        resolved = (self.product_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not resolved.is_relative_to(self.product_root):
            raise ValueError("source_path must stay within the product workspace")
        if resolved.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
            raise ValueError("source_path must be a .md, .txt, or .pdf file")
        if not resolved.is_file():
            raise FileNotFoundError(f"revision source does not exist: {resolved}")
        if resolved.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("revision source exceeds the 10 MiB local-workbench limit")
        return resolved

    def _snapshot_source(self, revision_id: str, source_path: Path) -> tuple[Path, str]:
        destination = self.runtime_root / "revisions" / revision_id / "source" / source_path.name
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(source_path, destination)
        digest = sha256_file(destination)
        return destination.resolve(), digest

    def _ensure_revision_capacity(self, additional_bytes: int) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source_path FROM revisions"
            ).fetchall()
        if len(rows) >= MAX_REVISIONS:
            raise WorkbenchConflictError(
                f"the local workbench supports at most {MAX_REVISIONS} revisions"
            )
        current_bytes = sum(
            Path(str(row["source_path"])).stat().st_size
            for row in rows
            if Path(str(row["source_path"])).is_file()
        )
        if current_bytes + additional_bytes > MAX_RUNTIME_SOURCE_BYTES:
            raise WorkbenchConflictError(
                "revision sources exceed the 500 MiB local-workbench quota"
            )

    def bootstrap_active_revision(self, *, engine_fingerprint: str = "") -> dict[str, Any]:
        active = self.get_active_revision(required=False)
        if active is not None:
            return active

        knowledge = get_active_knowledge()
        revision_id = uuid.uuid4().hex
        source = self._resolve_source(knowledge.source_path)
        snapshot, digest = self._snapshot_source(revision_id, source)
        now = utc_now()
        config = knowledge.public_dict()
        config["source_path"] = str(snapshot)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT active_revision_id FROM workbench_state WHERE singleton = 1"
            ).fetchone()
            if existing is not None:
                return self.get_revision(str(existing["active_revision_id"]))
            connection.execute(
                """
                INSERT INTO revisions(
                    id, parent_revision_id, label, source_name, source_path, source_sha256,
                    config_json, created_engine_fingerprint, created_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    knowledge.title,
                    source.name,
                    str(snapshot),
                    digest,
                    self._json(config),
                    engine_fingerprint,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO workbench_state(singleton, active_revision_id, updated_at) VALUES (1, ?, ?)",
                (revision_id, now),
            )
            self._append_event(
                connection,
                "revision_bootstrapped",
                revision_id=revision_id,
                payload={"source_sha256": digest},
            )
            self._append_event(connection, "revision_activated", revision_id=revision_id)
        return self.get_revision(revision_id)

    def create_revision(
        self,
        *,
        source_path: str | Path,
        label: str,
        engine_fingerprint: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = self._resolve_source(source_path)
        self._ensure_revision_capacity(source.stat().st_size)
        revision_id = uuid.uuid4().hex
        snapshot, digest = self._snapshot_source(revision_id, source)
        parent = self.get_active_revision()
        inherited = dict(parent["config"])
        inherited.update(config or {})
        inherited["source_path"] = str(snapshot)
        inherited["source"] = source.name
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO revisions(
                    id, parent_revision_id, label, source_name, source_path, source_sha256,
                    config_json, created_engine_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    parent["id"],
                    label,
                    source.name,
                    str(snapshot),
                    digest,
                    self._json(inherited),
                    engine_fingerprint,
                    now,
                ),
            )
            self._append_event(
                connection,
                "revision_created",
                revision_id=revision_id,
                payload={"parent_revision_id": parent["id"], "source_sha256": digest},
            )
            self._insert_adjustment(
                connection,
                revision_id=revision_id,
                category="knowledge_revision",
                summary=label,
                details={
                    "decision": "draft",
                    "before_sha": parent["source_sha256"],
                    "after_sha": digest,
                },
            )
        return self.get_revision(revision_id)

    def create_revision_from_content(
        self,
        *,
        content: str,
        label: str,
        engine_fingerprint: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        encoded = content.encode("utf-8")
        if not encoded.strip():
            raise ValueError("revision content must not be blank")
        if len(encoded) > MAX_SOURCE_BYTES:
            raise ValueError("revision content exceeds the 10 MiB local-workbench limit")
        self._ensure_revision_capacity(len(encoded))
        revision_id = uuid.uuid4().hex
        source_name = f"revision-{revision_id}.md"
        snapshot = self.runtime_root / "revisions" / revision_id / "source" / source_name
        snapshot.parent.mkdir(parents=True, exist_ok=False)
        snapshot.write_bytes(encoded)
        digest = sha256_file(snapshot)
        parent = self.get_active_revision()
        inherited = dict(parent["config"])
        inherited.update(config or {})
        inherited["source_path"] = str(snapshot.resolve())
        inherited["source"] = source_name
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO revisions(
                    id, parent_revision_id, label, source_name, source_path, source_sha256,
                    config_json, created_engine_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    parent["id"],
                    label,
                    source_name,
                    str(snapshot.resolve()),
                    digest,
                    self._json(inherited),
                    engine_fingerprint,
                    now,
                ),
            )
            self._append_event(
                connection,
                "revision_created",
                revision_id=revision_id,
                payload={"parent_revision_id": parent["id"], "source_sha256": digest},
            )
            self._insert_adjustment(
                connection,
                revision_id=revision_id,
                category="knowledge_revision",
                summary=label,
                details={
                    "decision": "draft",
                    "before_sha": parent["source_sha256"],
                    "after_sha": digest,
                },
            )
        return self.get_revision(revision_id)

    def create_revision_from_bytes(
        self,
        *,
        content: bytes,
        source_name: str,
        label: str,
        engine_fingerprint: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_name = Path(source_name).name
        if safe_name != source_name or not safe_name:
            raise ValueError("source filename must not contain a path")
        if Path(safe_name).suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
            raise ValueError("source file must be .md, .txt, or .pdf")
        if not content:
            raise ValueError("revision file must not be empty")
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("revision file exceeds the 10 MiB local-workbench limit")
        if Path(safe_name).suffix.lower() == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("uploaded PDF header is invalid")
        self._ensure_revision_capacity(len(content))

        revision_id = uuid.uuid4().hex
        snapshot = self.runtime_root / "revisions" / revision_id / "source" / safe_name
        snapshot.parent.mkdir(parents=True, exist_ok=False)
        snapshot.write_bytes(content)
        digest = sha256_file(snapshot)
        parent = self.get_active_revision()
        inherited = dict(parent["config"])
        inherited.update(config or {})
        inherited["source_path"] = str(snapshot.resolve())
        inherited["source"] = safe_name
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO revisions(
                    id, parent_revision_id, label, source_name, source_path, source_sha256,
                    config_json, created_engine_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    parent["id"],
                    label,
                    safe_name,
                    str(snapshot.resolve()),
                    digest,
                    self._json(inherited),
                    engine_fingerprint,
                    now,
                ),
            )
            self._append_event(
                connection,
                "revision_created",
                revision_id=revision_id,
                payload={"parent_revision_id": parent["id"], "source_sha256": digest},
            )
            self._insert_adjustment(
                connection,
                revision_id=revision_id,
                category="knowledge_revision",
                summary=label,
                details={
                    "decision": "draft",
                    "before_sha": parent["source_sha256"],
                    "after_sha": digest,
                },
            )
        return self.get_revision(revision_id)

    def _revision_dict(self, row: sqlite3.Row, *, active_id: str | None = None) -> dict[str, Any]:
        result = dict(row)
        result["config"] = self._decode(result.pop("config_json"), {})
        result["active"] = result["id"] == active_id
        return result

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            active = connection.execute(
                "SELECT active_revision_id FROM workbench_state WHERE singleton = 1"
            ).fetchone()
            row = connection.execute("SELECT * FROM revisions WHERE id = ?", (revision_id,)).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(f"revision not found: {revision_id}")
        return self._revision_dict(row, active_id=str(active["active_revision_id"]) if active else None)

    # `required=True` (the default) raises instead of returning None, so callers that
    # take the default get a plain dict. Declaring the union unconditionally made every
    # `get_active_revision()["id"]` in this repo a type error — 25 of the 40 mypy
    # errors that kept the type gate out of CI — and pushed callers toward asserts that
    # would only paper over the real signature.
    @overload
    def get_active_revision(self, *, required: Literal[True] = ...) -> dict[str, Any]: ...

    @overload
    def get_active_revision(self, *, required: bool) -> dict[str, Any] | None: ...

    def get_active_revision(self, *, required: bool = True) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.* FROM workbench_state s
                JOIN revisions r ON r.id = s.active_revision_id
                WHERE s.singleton = 1
                """
            ).fetchone()
        if row is None:
            if required:
                raise WorkbenchNotFoundError("active revision is not initialized")
            return None
        return self._revision_dict(row, active_id=str(row["id"]))

    def list_revisions(self) -> list[dict[str, Any]]:
        active = self.get_active_revision(required=False)
        active_id = active["id"] if active else None
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM revisions ORDER BY created_at DESC, id DESC").fetchall()
            decisions = connection.execute(
                """
                SELECT revision_id, event_type FROM events
                WHERE event_type IN ('revision_approved', 'revision_rejected')
                ORDER BY id
                """
            ).fetchall()
        latest_decision = {str(row["revision_id"]): str(row["event_type"]).removeprefix("revision_") for row in decisions}
        results = [self._revision_dict(row, active_id=active_id) for row in rows]
        for result in results:
            result["decision"] = latest_decision.get(result["id"])
        return results

    def revision_has_validation_or_decision(self, revision_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM jobs
                WHERE revision_id = ? AND kind = 'validation'
                UNION ALL
                SELECT 1 FROM events
                WHERE revision_id = ?
                  AND event_type IN ('revision_approved', 'revision_rejected')
                LIMIT 1
                """,
                (revision_id, revision_id),
            ).fetchone()
        return row is not None

    def create_job(
        self,
        *,
        revision_id: str,
        kind: str,
        engine_fingerprint: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_revision(revision_id)
        if kind not in {"comparison", "validation"}:
            raise ValueError("unsupported job kind")
        job_id = uuid.uuid4().hex
        now = utc_now()
        with self._connect() as connection:
            active_jobs = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running')"
            ).fetchone()[0]
            if int(active_jobs) >= MAX_ACTIVE_JOBS:
                raise WorkbenchConflictError(
                    f"at most {MAX_ACTIVE_JOBS} comparison or validation jobs may be active"
                )
            connection.execute(
                """
                INSERT INTO jobs(
                    id, revision_id, kind, status, engine_fingerprint, request_json, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (job_id, revision_id, kind, engine_fingerprint, self._json(request or {}), now),
            )
            self._append_event(
                connection,
                "job_created",
                revision_id=revision_id,
                job_id=job_id,
                payload={"kind": kind, "engine_fingerprint": engine_fingerprint},
            )
        return self.get_job(job_id)

    def create_async_run(
        self,
        *,
        revision_id: str,
        question: str,
        answer_mode: str,
    ) -> dict[str, Any]:
        self.get_revision(revision_id)
        run_id = uuid.uuid4().hex
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO async_runs(
                    id, revision_id, question, answer_mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (run_id, revision_id, question, answer_mode, now, now),
            )
            self._append_event(
                connection,
                "queued",
                revision_id=revision_id,
                run_id=run_id,
                payload={
                    "stage": "queued",
                    "summary": "Run accepted for asynchronous processing.",
                },
            )
        return self.get_async_run(run_id)

    def append_run_event(
        self,
        run_id: str,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision_id FROM async_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"async run not found: {run_id}")
            self._append_event(
                connection,
                event_type,
                revision_id=str(row["revision_id"]),
                run_id=run_id,
                payload=payload or {},
            )

    def mark_async_run_running(self, run_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, revision_id FROM async_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"async run not found: {run_id}")
            if row["status"] != "queued":
                raise WorkbenchConflictError(f"async run cannot start from status {row['status']}")
            connection.execute(
                """
                UPDATE async_runs SET status = 'running', updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), run_id),
            )
            self._append_event(
                connection,
                "running",
                revision_id=str(row["revision_id"]),
                run_id=run_id,
                payload={"stage": "running", "summary": "Run worker started."},
            )

    def finish_async_run(self, run_id: str, response: dict[str, Any]) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, revision_id FROM async_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"async run not found: {run_id}")
            if row["status"] not in {"queued", "running"}:
                raise WorkbenchConflictError(f"async run cannot finish from status {row['status']}")
            connection.execute(
                """
                UPDATE async_runs
                SET status = 'completed', response_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (self._json(response), utc_now(), run_id),
            )
            self._append_event(
                connection,
                "completed",
                revision_id=str(row["revision_id"]),
                run_id=run_id,
                payload={
                    "stage": "completed",
                    "delivery_status": response.get("delivery_status"),
                    "blocked_reason": response.get("blocked_reason"),
                },
            )

    def fail_async_run(self, run_id: str, error_message: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, revision_id FROM async_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"async run not found: {run_id}")
            connection.execute(
                """
                UPDATE async_runs
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (error_message, utc_now(), run_id),
            )
            self._append_event(
                connection,
                "failed",
                revision_id=str(row["revision_id"]),
                run_id=run_id,
                payload={"stage": "failed", "error": error_message},
            )

    def get_async_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM async_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(f"async run not found: {run_id}")
        result = dict(row)
        result["response"] = self._decode(result.pop("response_json"), None)
        return result

    def list_run_events(self, run_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM async_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if exists is None:
                raise WorkbenchNotFoundError(f"async run not found: {run_id}")
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE run_id = ? AND id > ?
                ORDER BY id
                """,
                (run_id, after_id),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["payload"] = self._decode(item.pop("payload_json"), {})
            results.append(item)
        return results

    def mark_job_running(self, job_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"job not found: {job_id}")
            updated = connection.execute(
                """
                UPDATE jobs SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (utc_now(), job_id),
            )
            if updated.rowcount != 1:
                current = connection.execute(
                    "SELECT status FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                raise WorkbenchConflictError(
                    f"job cannot start from status {current['status']}"
                )
            self._append_event(
                connection,
                "job_started",
                revision_id=str(row["revision_id"]),
                job_id=job_id,
            )

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {"passed", "failed", "error"}:
            raise ValueError("invalid terminal job status")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"job not found: {job_id}")
            if row["status"] != "running":
                raise WorkbenchConflictError(f"job cannot finish from status {row['status']}")
            connection.execute(
                """
                UPDATE jobs SET status = ?, result_json = ?, error_message = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, self._json(result) if result is not None else None, error_message, utc_now(), job_id),
            )
            self._append_event(
                connection,
                f"job_{status}",
                revision_id=str(row["revision_id"]),
                job_id=job_id,
                payload={"has_result": result is not None},
            )
            if status == "failed":
                self._insert_adjustment(
                    connection,
                    revision_id=str(row["revision_id"]),
                    job_id=job_id,
                    category="validation",
                    summary="Validation did not meet the release gate.",
                    details={"kind": str(row["kind"])},
                )

    def recover_interrupted_jobs(self, *, stale_after_seconds: int = 15 * 60) -> int:
        cutoff = (
            datetime.now(UTC) - timedelta(seconds=max(0, stale_after_seconds))
        ).isoformat(timespec="milliseconds")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, revision_id FROM jobs
                WHERE status IN ('pending', 'running')
                  AND COALESCE(started_at, created_at) <= ?
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE jobs SET status = 'interrupted', finished_at = ? WHERE id = ?
                    """,
                    (utc_now(), str(row["id"])),
                )
                self._append_event(
                    connection,
                    "job_interrupted",
                    revision_id=str(row["revision_id"]),
                    job_id=str(row["id"]),
                    payload={"reason": "process_restart"},
                )
        return len(rows)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(f"job not found: {job_id}")
        result = dict(row)
        result["request"] = self._decode(result.pop("request_json"), {})
        result["result"] = self._decode(result.pop("result_json"), None)
        return result

    def _coverage_candidate_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["external_answer"] = self._decode(result.pop("external_answer_json"), {})
        result["knowledge_answer"] = self._decode(result.pop("knowledge_answer_json"), {})
        result["fact_check"] = self._decode(result.pop("fact_check_json"), {})
        return result

    def save_coverage_loop_items(
        self,
        revision_id: str,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist coverage-loop items into the coverage-candidate ledger (design-doc step 3).

        `items` are `CoverageLoopItem.model_dump()` dicts from `run_coverage_loop`;
        each one's `disposition` is mapped to an initial ledger status via
        `COVERAGE_DISPOSITION_TO_STATUS` — see that constant's docstring for why
        `add_candidate` does not land directly on `auto_approved`.
        """
        self.get_revision(revision_id)
        now = utc_now()
        candidate_ids: list[str] = []
        with self._connect() as connection:
            for item in items:
                disposition = item["disposition"]
                status = COVERAGE_DISPOSITION_TO_STATUS[disposition]
                fact_check = item["fact_check"]
                candidate_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO coverage_candidates(
                        id, revision_id, question, intent, disposition, failure_cause,
                        candidate_reason, external_answer_json, knowledge_answer_json,
                        fact_check_json, status, status_reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        revision_id,
                        item["question"],
                        item.get("intent", ""),
                        disposition,
                        fact_check.get("failure_cause"),
                        item.get("candidate_reason", ""),
                        self._json(item["external_answer"]),
                        self._json(item["knowledge_answer"]),
                        self._json(fact_check),
                        status,
                        f"auto: {disposition}",
                        now,
                        now,
                    ),
                )
                self._append_event(
                    connection,
                    "coverage_candidate_created",
                    revision_id=revision_id,
                    payload={"candidate_id": candidate_id, "disposition": disposition, "status": status},
                )
                candidate_ids.append(candidate_id)
        return [self.get_coverage_candidate(candidate_id) for candidate_id in candidate_ids]

    def list_coverage_candidates(
        self,
        revision_id: str | None = None,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM coverage_candidates"
        conditions: list[str] = []
        params: list[Any] = []
        if revision_id is not None:
            conditions.append("revision_id = ?")
            params.append(revision_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._coverage_candidate_dict(row) for row in rows]

    def get_coverage_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM coverage_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(f"coverage candidate not found: {candidate_id}")
        return self._coverage_candidate_dict(row)

    def resolve_coverage_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        reason: str,
        operator: str | None = None,
    ) -> dict[str, Any]:
        """Manually resolve a quarantined or auto-rejected coverage candidate (step 5).

        Resolution lands on `auto_approved` / `auto_rejected` only — this is the
        "隔離だけまとめてユーザー確認する" path, not a general status editor. It starts
        from `auto_quarantined` as designed, and also from `auto_rejected`, because that
        state is reached by a judgment that measurement showed to be a coin flip; see
        `COVERAGE_RESOLVABLE_STATUSES`.
        """
        if status not in COVERAGE_QUARANTINE_RESOLUTIONS:
            raise ValueError(
                f"coverage candidates can only be resolved to one of {sorted(COVERAGE_QUARANTINE_RESOLUTIONS)}"
            )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM coverage_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise WorkbenchNotFoundError(f"coverage candidate not found: {candidate_id}")
            if row["status"] not in COVERAGE_RESOLVABLE_STATUSES:
                raise WorkbenchConflictError(
                    f"coverage candidate cannot be resolved from status {row['status']}"
                )
            connection.execute(
                """
                UPDATE coverage_candidates SET status = ?, status_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, reason, utc_now(), candidate_id),
            )
            self._append_event(
                connection,
                "coverage_candidate_resolved",
                revision_id=str(row["revision_id"]),
                payload={"candidate_id": candidate_id, "status": status, "reason": reason, "operator": operator},
            )
        return self.get_coverage_candidate(candidate_id)

    def _insert_adjustment(
        self,
        connection: sqlite3.Connection,
        *,
        revision_id: str,
        category: str,
        summary: str,
        details: dict[str, Any],
        run_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO adjustments(
                id, revision_id, run_id, job_id, category, summary, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                revision_id,
                run_id,
                job_id,
                category,
                summary,
                self._json(details),
                utc_now(),
            ),
        )

    def record_run(
        self,
        *,
        run_id: str,
        revision_id: str,
        question: str,
        candidate_answer: str,
        delivery_status: str,
        blocked_reason: str | None,
        verification: dict[str, Any],
        sources: list[dict[str, Any]],
        trace_id: str,
        audit_status: str,
        trace_url: str | None,
        job_id: str | None = None,
    ) -> None:
        if delivery_status not in {"released", "blocked"}:
            raise ValueError("invalid delivery status")
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, revision_id, job_id, question, candidate_answer, delivery_status,
                    blocked_reason, verification_json, sources_json, trace_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    revision_id,
                    job_id,
                    question,
                    candidate_answer,
                    delivery_status,
                    blocked_reason,
                    self._json(verification),
                    self._json(sources),
                    trace_id,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO traces(trace_id, run_id, status, trace_url, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trace_id, run_id, audit_status, trace_url, now),
            )
            self._append_event(
                connection,
                "answer_released" if delivery_status == "released" else "answer_blocked",
                revision_id=revision_id,
                job_id=job_id,
                run_id=run_id,
                payload={"blocked_reason": blocked_reason},
            )
            if delivery_status == "blocked":
                self._insert_adjustment(
                    connection,
                    revision_id=revision_id,
                    run_id=run_id,
                    job_id=job_id,
                    category="online_verification",
                    summary="Candidate answer was withheld by the quality gate.",
                    details={
                        "blocked_reason": blocked_reason,
                        "verification_status": verification.get("status"),
                    },
                )

    def list_adjustments(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, t.trace_id, t.trace_url, t.status AS trace_status,
                       r.question, r.delivery_status, r.blocked_reason,
                       r.verification_json, j.kind AS job_kind,
                       j.status AS job_status, j.request_json, j.result_json,
                       rev.source_sha256 AS revision_sha,
                       rev.config_json AS revision_config_json,
                       parent.source_sha256 AS parent_sha
                FROM adjustments a
                LEFT JOIN traces t ON t.run_id = a.run_id
                LEFT JOIN runs r ON r.id = a.run_id
                LEFT JOIN jobs j ON j.id = a.job_id
                JOIN revisions rev ON rev.id = a.revision_id
                LEFT JOIN revisions parent ON parent.id = rev.parent_revision_id
                ORDER BY a.created_at DESC, a.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            decision_rows = connection.execute(
                """
                SELECT revision_id, event_type, payload_json, created_at
                FROM events
                WHERE event_type IN ('revision_approved', 'revision_rejected')
                ORDER BY id
                """
            ).fetchall()
        decisions = {
            str(row["revision_id"]): {
                "status": str(row["event_type"]).removeprefix("revision_"),
                "payload": self._decode(row["payload_json"], {}),
                "created_at": row["created_at"],
            }
            for row in decision_rows
        }
        results = []
        for row in rows:
            item = dict(row)
            item["details"] = self._decode(item.pop("details_json"), {})
            verification = self._decode(item.pop("verification_json"), {})
            request = self._decode(item.pop("request_json"), {})
            job_result = self._decode(item.pop("result_json"), {})
            revision_config = self._decode(
                item.pop("revision_config_json"),
                {},
            )
            decision = decisions.get(str(item["revision_id"]))
            before = (job_result or {}).get("before") or {}
            after = (job_result or {}).get("after") or {}
            before_items = before.get("items") or []
            after_items = after.get("items") or []
            source_hashes = (job_result or {}).get("source_hashes") or {}
            item.update(
                {
                    "question": item.get("question") or request.get("question"),
                    "problem_stage": {
                        "online_verification": "回答照合・出荷判定",
                        "validation": "回帰検査",
                    }.get(str(item["category"]), str(item["category"])),
                    "cause_category": (
                        item.get("blocked_reason")
                        or item["details"].get("blocked_reason")
                        or item["category"]
                    ),
                    "knowledge_sha_before": (
                        source_hashes.get("before")
                        or item["details"].get("before_sha")
                        or item.get("parent_sha")
                    ),
                    "knowledge_sha_after": (
                        source_hashes.get("after")
                        or item["details"].get("after_sha")
                        or item.get("revision_sha")
                    ),
                    "change_content": (
                        revision_config.get("change_summary") or item["summary"]
                    ),
                    "before_answers": [
                        entry.get("answer") for entry in before_items
                    ],
                    "after_answers": [
                        entry.get("answer") for entry in after_items
                    ],
                    "claim_assessments": (
                        verification.get("claims")
                        or [
                            claim
                            for entry in after_items
                            for claim in (
                                (entry.get("verification") or {}).get("claims")
                                or []
                            )
                        ]
                    ),
                    "score_deltas": [
                        {
                            "id": after_entry.get("id"),
                            "before": (
                                before_items[index].get("axes")
                                if index < len(before_items)
                                else {}
                            ),
                            "after": after_entry.get("axes") or {},
                        }
                        for index, after_entry in enumerate(after_items)
                    ],
                    "side_effects": (
                        (job_result or {}).get("regression_item_ids") or []
                    ),
                    "decision": (
                        decision["status"]
                        if decision
                        else item["details"].get("decision", "hold")
                    ),
                    "decision_reason": (
                        (decision or {}).get("payload", {}).get("reason")
                        or item["details"].get("reason")
                        or item.get("blocked_reason")
                        or item["details"].get("blocked_reason")
                    ),
                    "operator": (
                        item["details"].get("operator")
                        or (decision or {}).get("payload", {}).get("operator")
                        or revision_config.get("operator")
                        or "local-operator"
                    ),
                    "langfuse_trace_id": item.get("trace_id"),
                }
            )
            results.append(item)
        return results

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT t.trace_id, t.run_id, t.status, t.trace_url, t.created_at,
                       r.revision_id, r.delivery_status
                FROM traces t JOIN runs r ON r.id = t.run_id
                WHERE t.trace_id = ?
                """,
                (trace_id,),
            ).fetchone()
        if row is None:
            raise WorkbenchNotFoundError(f"trace not found: {trace_id}")
        return dict(row)

    def reject_revision(
        self,
        revision_id: str,
        *,
        reason: str,
        operator: str | None = None,
    ) -> dict[str, Any]:
        revision = self.get_revision(revision_id)
        if revision["active"]:
            raise WorkbenchConflictError("the active revision cannot be rejected")
        with self._connect() as connection:
            self._append_event(
                connection,
                "revision_rejected",
                revision_id=revision_id,
                payload={"reason": reason, "operator": operator},
            )
            self._insert_adjustment(
                connection,
                revision_id=revision_id,
                category="decision",
                summary="Revision rejected.",
                details={
                    "decision": "rejected",
                    "reason": reason,
                    "operator": operator,
                },
            )
        return self.get_revision(revision_id)

    def approve_revision(
        self,
        revision_id: str,
        *,
        reason: str,
        engine_fingerprint: str | None = None,
        structured_digest: str | None = None,
        operator: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision = connection.execute(
                """
                SELECT id, source_path, source_sha256, config_json,
                       created_engine_fingerprint
                FROM revisions WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise WorkbenchNotFoundError(f"revision not found: {revision_id}")
            rejected = connection.execute(
                """
                SELECT 1 FROM events
                WHERE revision_id = ? AND event_type = 'revision_rejected'
                LIMIT 1
                """,
                (revision_id,),
            ).fetchone()
            validation = connection.execute(
                """
                SELECT * FROM jobs
                WHERE revision_id = ? AND kind = 'validation'
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (revision_id,),
            ).fetchone()
            result = self._decode(validation["result_json"], {}) if validation is not None else {}
            config = self._decode(revision["config_json"], {})
            eval_set = config.get("eval_set")
            eval_set_hash_matches = False
            if eval_set:
                eval_path = Path(str(eval_set))
                eval_path = (
                    self.product_root / eval_path
                ).resolve() if not eval_path.is_absolute() else eval_path.resolve()
                eval_set_hash_matches = (
                    eval_path.is_relative_to(self.product_root)
                    and eval_path.is_file()
                    and sha256_file(eval_path) == result.get("eval_set_sha256")
                )
            source_hash_matches = (
                Path(str(revision["source_path"])).is_file()
                and sha256_file(Path(str(revision["source_path"])))
                == revision["source_sha256"]
                and (result.get("source_hashes") or {}).get("after")
                == revision["source_sha256"]
            )
            structured_hashes = result.get("structured_hashes") or {}
            structured_hash_matches = (
                structured_digest is None
                if not structured_hashes
                else (
                    structured_digest is not None
                    and structured_hashes.get("after") == structured_digest
                )
            )
            if (
                rejected is not None
                or validation is None
                or validation["status"] != "passed"
                or result.get("full_pass") is not True
                or result.get("no_regression") is not True
                or not source_hash_matches
                or not structured_hash_matches
                or not eval_set_hash_matches
                or (
                    engine_fingerprint is not None
                    and (
                        validation["engine_fingerprint"] != engine_fingerprint
                        or revision["created_engine_fingerprint"] != engine_fingerprint
                    )
                )
            ):
                raise WorkbenchConflictError(
                    "approval requires the latest full validation to pass with no regression under the current engine settings"
                )
            connection.execute(
                """
                INSERT INTO workbench_state(singleton, active_revision_id, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE
                SET active_revision_id = excluded.active_revision_id,
                    updated_at = excluded.updated_at
                """,
                (revision_id, now),
            )
            self._append_event(
                connection,
                "revision_approved",
                revision_id=revision_id,
                job_id=str(validation["id"]),
                payload={"reason": reason, "operator": operator},
            )
            self._append_event(
                connection,
                "revision_activated",
                revision_id=revision_id,
                job_id=str(validation["id"]),
            )
            self._insert_adjustment(
                connection,
                revision_id=revision_id,
                job_id=str(validation["id"]),
                category="decision",
                summary="Revision approved and activated.",
                details={
                    "decision": "approved",
                    "reason": reason,
                    "operator": operator,
                    "validation_job_id": str(validation["id"]),
                },
            )
        return self.get_revision(revision_id)

    def list_events(self, *, revision_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        params: tuple[Any, ...] = ()
        if revision_id is not None:
            query += " WHERE revision_id = ?"
            params = (revision_id,)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["payload"] = self._decode(item.pop("payload_json"), {})
            results.append(item)
        return results
