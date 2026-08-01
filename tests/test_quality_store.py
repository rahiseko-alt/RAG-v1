import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.quality import WorkbenchConflictError, WorkbenchStore  # noqa: E402
from src.rag import engine_fingerprint  # noqa: E402
from src.quality.store import sha256_file  # noqa: E402


def _store(tmp_path):
    return WorkbenchStore(
        tmp_path / "workbench.sqlite3",
        runtime_root=tmp_path / "runtime",
        engine_fingerprint=engine_fingerprint(),
    )


def _passing_validation_result(store, revision):
    eval_path = Path(_ROOT) / revision["config"]["eval_set"]
    return {
        "full_pass": True,
        "no_regression": True,
        "source_hashes": {"after": revision["source_sha256"]},
        "eval_set_sha256": sha256_file(eval_path),
    }


def test_bootstrap_snapshots_active_source_and_appends_events(tmp_path):
    store = _store(tmp_path)

    active = store.get_active_revision()
    snapshot = active["source_path"]

    assert active["active"] is True
    assert len(active["source_sha256"]) == 64
    assert os.path.isfile(snapshot)
    assert str(snapshot).startswith(str(tmp_path))
    assert [event["event_type"] for event in store.list_events()] == [
        "revision_bootstrapped",
        "revision_activated",
    ]


def test_revisions_and_events_are_database_enforced_immutable(tmp_path):
    store = _store(tmp_path)
    active = store.get_active_revision()

    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="revisions are immutable"):
            connection.execute(
                "UPDATE revisions SET label = 'changed' WHERE id = ?",
                (active["id"],),
            )

    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="events are append-only"):
            connection.execute("DELETE FROM events")


def test_running_job_becomes_interrupted_after_restart(tmp_path):
    store = _store(tmp_path)
    active = store.get_active_revision()
    job = store.create_job(
        revision_id=active["id"],
        kind="comparison",
        engine_fingerprint=engine_fingerprint(),
    )
    store.mark_job_running(job["id"])

    restarted = WorkbenchStore(
        store.db_path,
        runtime_root=store.runtime_root,
        bootstrap=False,
    )
    restarted.recover_interrupted_jobs(stale_after_seconds=0)

    assert restarted.get_job(job["id"])["status"] == "interrupted"
    assert restarted.list_events()[-1]["event_type"] == "job_interrupted"


def test_job_start_is_single_transition_and_stale_pending_is_recovered(tmp_path):
    store = _store(tmp_path)
    active = store.get_active_revision()
    job = store.create_job(
        revision_id=active["id"],
        kind="comparison",
        engine_fingerprint=engine_fingerprint(),
    )

    store.mark_job_running(job["id"])
    with pytest.raises(WorkbenchConflictError):
        store.mark_job_running(job["id"])

    assert sum(
        event["event_type"] == "job_started"
        for event in store.list_events()
    ) == 1


def test_approval_requires_latest_successful_full_validation(tmp_path):
    store = _store(tmp_path)
    revision = store.create_revision(
        source_path="data/sample/jujutsu-kaisen-wikipedia.md",
        label="candidate",
        engine_fingerprint=engine_fingerprint(),
    )

    with pytest.raises(WorkbenchConflictError):
        store.approve_revision(revision["id"], reason="")

    job = store.create_job(
        revision_id=revision["id"],
        kind="validation",
        engine_fingerprint=engine_fingerprint(),
    )
    store.mark_job_running(job["id"])
    store.finish_job(
        job["id"],
        status="passed",
        result=_passing_validation_result(store, revision),
    )

    approved = store.approve_revision(revision["id"], reason="validated")

    assert approved["active"] is True
    assert store.get_active_revision()["id"] == revision["id"]


def test_approval_rejects_engine_drift_and_active_revision_rejection(tmp_path):
    store = _store(tmp_path)
    active = store.get_active_revision()
    with pytest.raises(WorkbenchConflictError, match="active revision"):
        store.reject_revision(active["id"], reason="must not reject active")

    revision = store.create_revision(
        source_path="data/sample/jujutsu-kaisen-wikipedia.md",
        label="candidate",
        engine_fingerprint=engine_fingerprint(),
    )
    job = store.create_job(
        revision_id=revision["id"],
        kind="validation",
        engine_fingerprint=engine_fingerprint(),
    )
    store.mark_job_running(job["id"])
    store.finish_job(
        job["id"],
        status="passed",
        result=_passing_validation_result(store, revision),
    )

    with pytest.raises(WorkbenchConflictError, match="current engine settings"):
        store.approve_revision(
            revision["id"],
            reason="stale result",
            engine_fingerprint="different-engine-fingerprint",
        )


def test_approval_rejects_rejected_or_tampered_revision(tmp_path):
    store = _store(tmp_path)
    rejected = store.create_revision(
        source_path="data/sample/jujutsu-kaisen-wikipedia.md",
        label="rejected candidate",
        engine_fingerprint=engine_fingerprint(),
    )
    rejected_job = store.create_job(
        revision_id=rejected["id"],
        kind="validation",
        engine_fingerprint=engine_fingerprint(),
    )
    store.mark_job_running(rejected_job["id"])
    store.finish_job(
        rejected_job["id"],
        status="passed",
        result=_passing_validation_result(store, rejected),
    )
    store.reject_revision(rejected["id"], reason="incorrect")

    with pytest.raises(WorkbenchConflictError):
        store.approve_revision(
            rejected["id"],
            reason="must remain rejected",
            engine_fingerprint=engine_fingerprint(),
        )

    tampered = store.create_revision(
        source_path="data/sample/jujutsu-kaisen-wikipedia.md",
        label="tampered candidate",
        engine_fingerprint=engine_fingerprint(),
    )
    tampered_job = store.create_job(
        revision_id=tampered["id"],
        kind="validation",
        engine_fingerprint=engine_fingerprint(),
    )
    store.mark_job_running(tampered_job["id"])
    store.finish_job(
        tampered_job["id"],
        status="passed",
        result=_passing_validation_result(store, tampered),
    )
    Path(tampered["source_path"]).write_text("tampered", encoding="utf-8")

    with pytest.raises(WorkbenchConflictError):
        store.approve_revision(
            tampered["id"],
            reason="hash mismatch",
            engine_fingerprint=engine_fingerprint(),
        )
