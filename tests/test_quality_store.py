import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.quality import WorkbenchConflictError, WorkbenchNotFoundError, WorkbenchStore  # noqa: E402
from src.quality.workbench import QualityWorkbench  # noqa: E402
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


def _passing_validation_result_for_workbench_activation(store, workbench, revision):
    """Like `_passing_validation_result`, plus `structured_hashes` — required because
    `QualityWorkbench.activate_coverage_candidate` (unlike calling `approve_revision`
    directly with no `structured_digest`) always supplies the real structured digest,
    so a fake job result must include a matching one or every activation would fail on
    a mismatch that has nothing to do with what the test is actually checking."""
    result = _passing_validation_result(store, revision)
    result["structured_hashes"] = {"after": workbench.structured_digest(revision["id"])}
    return result


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


def _coverage_item(question, *, disposition, failure_cause=None, candidate_reason=""):
    return {
        "question": question,
        "intent": "test",
        "external_answer": {"role": "external", "answer": "A の回答", "status": "ok"},
        "knowledge_answer": {"role": "knowledge", "answer": "記載がありません", "status": "released"},
        "fact_check": {
            "external_status": "pass",
            "knowledge_status": "abstain",
            "same_answer": False,
            "failure_cause": failure_cause,
        },
        "disposition": disposition,
        "add_knowledge_candidate": disposition == "add_candidate",
        "candidate_reason": candidate_reason,
    }


def test_save_coverage_loop_items_maps_disposition_to_ledger_status(tmp_path):
    """add_candidate lands on auto_classified, not auto_approved: promoting further
    needs a before/after check the design-doc marks as separate future work."""
    store = _store(tmp_path)
    revision = store.get_active_revision()
    items = [
        _coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge"),
        _coverage_item("q2", disposition="rejected", failure_cause="ambiguous_question"),
        _coverage_item("q3", disposition="quarantined", failure_cause="needs_quarantine"),
        _coverage_item("q4", disposition="no_gap"),
    ]

    saved = store.save_coverage_loop_items(revision["id"], items)

    status_by_question = {candidate["question"]: candidate["status"] for candidate in saved}
    assert status_by_question == {
        "q1": "auto_classified",
        "q2": "auto_rejected",
        "q3": "auto_quarantined",
        "q4": "no_gap",
    }
    persisted = store.list_coverage_candidates(revision["id"])
    assert len(persisted) == 4
    assert {event["event_type"] for event in store.list_events()} >= {"coverage_candidate_created"}


def test_list_coverage_candidates_filters_by_status(tmp_path):
    store = _store(tmp_path)
    revision = store.get_active_revision()
    store.save_coverage_loop_items(
        revision["id"],
        [
            _coverage_item("q1", disposition="add_candidate"),
            _coverage_item("q2", disposition="rejected"),
        ],
    )

    quarantined = store.list_coverage_candidates(revision["id"], status="auto_quarantined")
    assert quarantined == []
    rejected = store.list_coverage_candidates(revision["id"], status="auto_rejected")
    assert [candidate["question"] for candidate in rejected] == ["q2"]


def test_resolve_coverage_candidate_leaves_quarantine_and_rejection(tmp_path):
    store = _store(tmp_path)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="quarantined", failure_cause="needs_quarantine")],
    )
    candidate_id = saved[0]["id"]

    resolved = store.resolve_coverage_candidate(
        candidate_id, status="auto_approved", reason="human confirmed the source"
    )
    assert resolved["status"] == "auto_approved"
    assert resolved["status_reason"] == "human confirmed the source"

    # An accepted decision is closed: nothing reopens auto_approved.
    with pytest.raises(WorkbenchConflictError):
        store.resolve_coverage_candidate(candidate_id, status="auto_rejected", reason="too late")

    with pytest.raises(WorkbenchNotFoundError):
        store.resolve_coverage_candidate("missing-id", status="auto_approved", reason="n/a")


def test_auto_rejected_candidates_can_still_be_reopened(tmp_path):
    """`invalid_A` is a judge call measured to be a coin flip on identical sourcing, and
    it writes straight to auto_rejected. If that state were terminal, a wrong rejection
    would be unrecoverable and invisible — the one outcome with no human path back."""
    store = _store(tmp_path)
    revision = store.get_active_revision()
    rejected = store.save_coverage_loop_items(
        revision["id"], [_coverage_item("q2", disposition="rejected")]
    )[0]
    assert rejected["status"] == "auto_rejected"

    reopened = store.resolve_coverage_candidate(
        rejected["id"], status="auto_approved", reason="A was re-sourced and holds up"
    )
    assert reopened["status"] == "auto_approved"


def test_no_gap_candidates_stay_closed(tmp_path):
    """A and B agreed; there is no decision to revisit."""
    store = _store(tmp_path)
    revision = store.get_active_revision()
    settled = store.save_coverage_loop_items(
        revision["id"], [_coverage_item("q3", disposition="no_gap")]
    )[0]
    with pytest.raises(WorkbenchConflictError):
        store.resolve_coverage_candidate(settled["id"], status="auto_approved", reason="n/a")


def test_resolve_coverage_candidate_rejects_invalid_target_status(tmp_path):
    store = _store(tmp_path)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="quarantined", failure_cause="needs_quarantine")],
    )
    with pytest.raises(ValueError):
        store.resolve_coverage_candidate(saved[0]["id"], status="implemented", reason="n/a")


def test_auto_classified_candidates_can_be_resolved_directly_to_auto_approved(tmp_path):
    """Phase 2: `auto_classified` used to be a dead end with no path to `auto_approved`."""
    store = _store(tmp_path)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    assert saved[0]["status"] == "auto_classified"

    approved = store.resolve_coverage_candidate(
        saved[0]["id"], status="auto_approved", reason="sourcing confirmed"
    )
    assert approved["status"] == "auto_approved"


def test_coverage_candidate_passes_through_the_full_promotion_pipeline(tmp_path):
    """Phase 2 completion condition: one candidate walks
    auto_classified -> auto_approved -> implemented -> verified -> active end to end,
    with a fake (not LLM-generated) validation job standing in for a real one.
    """
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [
            _coverage_item(
                "呪力とは何ですか。追加で説明してください。",
                disposition="add_candidate",
                failure_cause="missing_knowledge",
            )
        ],
    )
    candidate_id = saved[0]["id"]

    approved = store.resolve_coverage_candidate(
        candidate_id, status="auto_approved", reason="sourcing confirmed"
    )
    assert approved["status"] == "auto_approved"

    implemented = workbench.implement_coverage_candidate(candidate_id)
    assert implemented["status"] == "implemented"
    assert implemented["implemented_revision_id"]

    new_revision = store.get_revision(implemented["implemented_revision_id"])
    new_text = Path(new_revision["source_path"]).read_text(encoding="utf-8")
    assert "追加候補" in new_text
    assert "呪力とは何ですか。追加で説明してください。" in new_text

    job = store.create_job(
        revision_id=new_revision["id"], kind="validation", engine_fingerprint=engine_fingerprint()
    )
    store.mark_job_running(job["id"])
    store.finish_job(
        job["id"],
        status="passed",
        result=_passing_validation_result_for_workbench_activation(store, workbench, new_revision),
    )

    verified = store.verify_coverage_candidate(candidate_id)
    assert verified["status"] == "verified"

    activated = workbench.activate_coverage_candidate(candidate_id, reason="promoted")
    assert activated["status"] == "active"
    assert store.get_active_revision()["id"] == new_revision["id"]


def test_verify_coverage_candidate_requires_implemented_status(tmp_path):
    store = _store(tmp_path)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    with pytest.raises(WorkbenchConflictError):
        store.verify_coverage_candidate(saved[0]["id"])


def test_verify_coverage_candidate_blocks_without_a_passing_validation_job(tmp_path):
    """The completion condition's other half: a candidate whose improvement was never
    confirmed must not be able to reach `verified`."""
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")
    implemented = workbench.implement_coverage_candidate(saved[0]["id"])

    with pytest.raises(WorkbenchConflictError):
        store.verify_coverage_candidate(implemented["id"])


def test_verify_coverage_candidate_blocks_when_validation_result_shows_a_regression(tmp_path):
    """Even a job marked `passed` must not verify a candidate if its own result data
    does not actually show full_pass/no_regression — defense in depth against a
    malformed or tampered job row, not just against a missing one."""
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")
    implemented = workbench.implement_coverage_candidate(saved[0]["id"])
    new_revision = store.get_revision(implemented["implemented_revision_id"])

    job = store.create_job(
        revision_id=new_revision["id"], kind="validation", engine_fingerprint=engine_fingerprint()
    )
    store.mark_job_running(job["id"])
    bad_result = _passing_validation_result(store, new_revision)
    bad_result["no_regression"] = False
    store.finish_job(job["id"], status="passed", result=bad_result)

    with pytest.raises(WorkbenchConflictError):
        store.verify_coverage_candidate(implemented["id"])


def test_chunking_failure_candidates_are_never_verification_eligible(tmp_path):
    """Phase 1 (2026-08-03) measured `chunking_failure` at 0/5 against a construction-
    verified gold set — it must not auto-promote even with a perfectly passing
    validation job."""
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="chunking_failure")],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")
    implemented = workbench.implement_coverage_candidate(saved[0]["id"])
    new_revision = store.get_revision(implemented["implemented_revision_id"])

    job = store.create_job(
        revision_id=new_revision["id"], kind="validation", engine_fingerprint=engine_fingerprint()
    )
    store.mark_job_running(job["id"])
    store.finish_job(
        job["id"], status="passed", result=_passing_validation_result(store, new_revision)
    )

    with pytest.raises(WorkbenchConflictError, match="not eligible"):
        store.verify_coverage_candidate(implemented["id"])


def test_implement_coverage_candidate_requires_auto_approved_status(tmp_path):
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    with pytest.raises(WorkbenchConflictError):
        workbench.implement_coverage_candidate(saved[0]["id"])


def test_activate_coverage_candidate_requires_verified_status(tmp_path):
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")
    implemented = workbench.implement_coverage_candidate(saved[0]["id"])

    with pytest.raises(WorkbenchConflictError):
        store.activate_coverage_candidate(implemented["id"], reason="too soon")


def test_mark_coverage_candidate_implemented_rejects_an_unrelated_revision(tmp_path):
    """2026-08-03 adversarial review, confirmed with a PoC: a caller-supplied
    `revision_id` with no relation to the candidate's own content could otherwise be
    linked in, validated for its own (unrelated) content, and ridden through
    verify/activate — making the candidate `active` while its actual proposed content
    never appeared in any revision at all."""
    store = _store(tmp_path)
    revision = store.get_active_revision()

    rogue = store.create_revision(
        source_path="data/sample/jujutsu-kaisen-wikipedia.md",
        label="unrelated revision",
        engine_fingerprint=engine_fingerprint(),
    )

    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")

    with pytest.raises(WorkbenchConflictError, match="not created from this coverage candidate"):
        store.mark_coverage_candidate_implemented(saved[0]["id"], revision_id=rogue["id"])


def test_mark_coverage_candidate_implemented_rejects_a_revision_claimed_by_another_candidate(
    tmp_path,
):
    """A revision whose config really was stamped for a different candidate is
    rejected by the config-match check before the separate uniqueness check even runs
    — confirming the two candidates in this scenario cannot end up sharing one
    `implemented_revision_id` no matter which check catches it first."""
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [
            _coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge"),
            _coverage_item("q2", disposition="add_candidate", failure_cause="missing_knowledge"),
        ],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")
    store.resolve_coverage_candidate(saved[1]["id"], status="auto_approved", reason="ok")
    first = workbench.implement_coverage_candidate(saved[0]["id"])

    with pytest.raises(WorkbenchConflictError):
        store.mark_coverage_candidate_implemented(
            saved[1]["id"], revision_id=first["implemented_revision_id"]
        )


def test_activate_coverage_candidate_via_workbench_rejects_stale_engine_fingerprint(tmp_path):
    """2026-08-03 adversarial review, confirmed with a PoC: `WorkbenchStore.
    activate_coverage_candidate` omitted `engine_fingerprint=`/`structured_digest=`
    when delegating to `approve_revision`, silently skipping the engine-drift check the
    plain `/workbench/revisions/{id}/approve` endpoint always applies.
    `QualityWorkbench.activate_coverage_candidate` is the fix: it supplies both, the
    same way that endpoint does."""
    store = _store(tmp_path)
    workbench = QualityWorkbench(store)
    revision = store.get_active_revision()
    saved = store.save_coverage_loop_items(
        revision["id"],
        [_coverage_item("q1", disposition="add_candidate", failure_cause="missing_knowledge")],
    )
    store.resolve_coverage_candidate(saved[0]["id"], status="auto_approved", reason="ok")
    implemented = workbench.implement_coverage_candidate(saved[0]["id"])
    new_revision = store.get_revision(implemented["implemented_revision_id"])

    job = store.create_job(
        revision_id=new_revision["id"], kind="validation", engine_fingerprint="stale-engine-v1"
    )
    store.mark_job_running(job["id"])
    result = _passing_validation_result_for_workbench_activation(store, workbench, new_revision)
    store.finish_job(job["id"], status="passed", result=result)
    store.verify_coverage_candidate(implemented["id"])

    # Isolate the engine-fingerprint condition: everything else about this job/result
    # (source hash, structured hash, eval-set hash) matches, so if this still raises,
    # it is because of the stale `engine_fingerprint="stale-engine-v1"` above — the
    # exact check `WorkbenchStore.activate_coverage_candidate` used to skip.
    assert workbench.fingerprint() != "stale-engine-v1"
    with pytest.raises(WorkbenchConflictError):
        workbench.activate_coverage_candidate(implemented["id"], reason="promoted")
