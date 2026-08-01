"""Agent loop for discovering knowledge gaps before editing the knowledge base."""
from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from src.rag import AnswerMode, build_chat_model, get_generation_model


AgentRole = Literal["external", "knowledge"]
FactStatus = Literal["pass", "fail", "abstain", "unclear"]

# D 判定が出す失敗原因の分類。
#
# このループが検出できるのは「B が A より弱く見えた差分」だけであり、その原因は
# ナレッジ不足とは限らない。原因を分けずに全て missing_knowledge として扱うと、
# 検索の問題や質問側の問題までナレッジ追加で解こうとして誤検知を量産する。
#
# - missing_knowledge  : ナレッジ自体に情報が無い（＝追加すべき）
# - retrieval_failure  : 情報はあるが検索で引けていない（検索側を直す）
# - generation_failure : チャンクは引けたが生成が使えていない（生成側を直す）
# - chunking_failure   : 分割が悪く情報が分断されている（ingest 側を直す）
# - ambiguous_question : 質問が多義的で A と B が別の解釈をしている
# - invalid_A          : A の回答自体が使えない（出典なし・推測・誤り）
# - out_of_scope       : そもそもこのナレッジが扱う範囲外
# - needs_quarantine   : 分類できない、または判断が割れた（人が見る）
FailureCause = Literal[
    "missing_knowledge",
    "retrieval_failure",
    "generation_failure",
    "chunking_failure",
    "ambiguous_question",
    "invalid_A",
    "out_of_scope",
    "needs_quarantine",
]

# 候補の行き先。ユーザー都度承認は行わない方針のため、通常候補は自動採用/自動却下に
# 振り分け、判断できないものだけ quarantined に落としてまとめて人が確認する。
Disposition = Literal["add_candidate", "rejected", "quarantined", "no_gap"]

# ナレッジ改善で解ける原因＝候補として拾う。
ADDABLE_CAUSES: frozenset[str] = frozenset(
    {"missing_knowledge", "retrieval_failure", "generation_failure", "chunking_failure"}
)
# 質問側・A側・スコープの問題＝ナレッジを足しても解決しないので却下する。
REJECTED_CAUSES: frozenset[str] = frozenset(
    {"ambiguous_question", "invalid_A", "out_of_scope"}
)


class CoverageQuestion(BaseModel):
    """A question generated to probe causal, hierarchical, or edge-case gaps."""

    question: str = Field(..., min_length=1)
    intent: str = ""


class EvidenceSource(BaseModel):
    """One reference backing an A-answer (session-report step 2: 出典URL/出典種別/根拠span/更新日).

    Kept optional on `AgentAnswer` so existing callers/fixtures that predate this
    taxonomy keep working unchanged. `classify_coverage_item` does not yet read
    this field — wiring evidence completeness into auto-reject/quarantine is step
    4 (ledger state transitions), tracked separately.
    """

    url: str = ""
    source_type: str = ""
    span: str = ""
    updated_at: str = ""


class RetrievedChunk(BaseModel):
    """One chunk B's retriever surfaced (session-report step 2/7: 取得chunk/score/引用箇所/検索順位).

    Recording this per-answer is what will let a later D judgment distinguish
    `missing_knowledge` (nothing relevant was retrievable) from `retrieval_failure`
    (a relevant chunk was retrieved but not used) instead of guessing from the
    final answer text alone.
    """

    chunk_id: str = ""
    score: float | None = None
    citation: str = ""
    rank: int | None = None


class AgentAnswer(BaseModel):
    """One answer produced by an agent participating in the coverage loop."""

    role: AgentRole
    answer: str | None = None
    status: str = "ok"
    sources: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""
    model: str | None = None
    confidence: float | None = None
    evidence: list[EvidenceSource] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)


class FactCheckJudgment(BaseModel):
    """D-agent judgment comparing external and knowledge-only answers.

    `failure_cause` is optional so existing callers (fixtures, manually supplied
    fact_checks) that predate the taxonomy keep working: when it is omitted, the
    loop falls back to the pre-taxonomy heuristic (see `classify_coverage_item`).
    Once a caller supplies it, D is treated as authoritative for *why* B lost to A,
    not just *whether* it did — this is what prevents "B looked weaker than A"
    from being auto-translated into "add to knowledge base" for every gap.
    """

    external_status: FactStatus
    knowledge_status: FactStatus
    same_answer: bool = False
    missing_knowledge: str = ""
    reason: str = ""
    failure_cause: FailureCause | None = None


class CoverageLoopItem(BaseModel):
    question: str
    intent: str = ""
    external_answer: AgentAnswer
    knowledge_answer: AgentAnswer
    fact_check: FactCheckJudgment
    disposition: Disposition = "no_gap"
    # Kept for backward compatibility with callers reading the pre-taxonomy field;
    # always equal to (disposition == "add_candidate").
    add_knowledge_candidate: bool
    candidate_reason: str = ""


class CoverageLoopResult(BaseModel):
    """Aggregated result of one coverage loop run."""

    revision_id: str
    focus: str | None = None
    rounds: int
    total_questions: int
    add_candidates: int
    # Counts per disposition / failure_cause, e.g. {"add_candidate": 3, "rejected": 2}.
    # Absent causes are simply not keys (no zero-fill), so an empty dict means every
    # judgment used the pre-taxonomy fallback (no failure_cause was ever supplied).
    disposition_counts: dict[str, int] = Field(default_factory=dict)
    cause_counts: dict[str, int] = Field(default_factory=dict)
    items: list[CoverageLoopItem]


class CoverageQuestionGenerator(Protocol):
    def generate(
        self,
        *,
        focus: str | None,
        seed_questions: list[str],
        previous_findings: list[CoverageLoopItem],
        max_questions: int,
    ) -> list[CoverageQuestion]:
        """Generate or normalize questions for one round."""


class ExternalAnswerer(Protocol):
    def answer(self, *, question: str) -> AgentAnswer:
        """Answer without relying on the local knowledge base."""


class FactChecker(Protocol):
    def check(
        self,
        *,
        question: str,
        external_answer: AgentAnswer,
        knowledge_answer: AgentAnswer,
    ) -> FactCheckJudgment:
        """Judge whether A is valid and B missed the needed knowledge."""


class MissingExternalAnswerer:
    """Fail if A-answer was not provided by a human/subagent/manual fixture."""

    def answer(self, *, question: str) -> AgentAnswer:
        raise ValueError(f"external answer is required for coverage-loop question: {question}")


class ProvidedFactChecker:
    """Use caller-supplied D judgments instead of calling another LLM."""

    def __init__(self, judgments: dict[str, Any]) -> None:
        self.judgments = judgments

    def check(
        self,
        *,
        question: str,
        external_answer: AgentAnswer,
        knowledge_answer: AgentAnswer,
    ) -> FactCheckJudgment:
        raw = self.judgments.get(question)
        if raw is None:
            raise ValueError(f"fact_check is required for coverage-loop question: {question}")
        if isinstance(raw, FactCheckJudgment):
            return raw
        if isinstance(raw, dict):
            return FactCheckJudgment.model_validate(raw)
        raise ValueError(f"invalid fact_check for coverage-loop question: {question}")


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "\n".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
    return str(content)


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if match:
        cleaned = match.group(1).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("coverage loop LLM response must be a JSON object")
    return payload


class LLMCoverageQuestionGenerator:
    """Generate domain-neutral gap-probing questions with the configured LLM."""

    def __init__(self, model: Any | None = None) -> None:
        self.model = model

    def _model(self) -> Any:
        if self.model is None:
            self.model = build_chat_model()
        return self.model

    def generate(
        self,
        *,
        focus: str | None,
        seed_questions: list[str],
        previous_findings: list[CoverageLoopItem],
        max_questions: int,
    ) -> list[CoverageQuestion]:
        normalized = [question.strip() for question in seed_questions if question.strip()]
        if normalized:
            return [
                CoverageQuestion(question=question, intent="seed")
                for question in normalized[:max_questions]
            ]

        findings = [
            {
                "question": item.question,
                "missing_knowledge": item.fact_check.missing_knowledge,
            }
            for item in previous_findings[-10:]
            if item.add_knowledge_candidate
        ]
        prompt = {
            "task": (
                "Generate Japanese questions that expose missing knowledge in a RAG knowledge base. "
                "Prefer causal relations, hierarchy, aliases, exceptions, chronology, and 'why/how' links. "
                "Do not answer the questions."
            ),
            "focus": focus or "",
            "previous_missing_findings": findings,
            "max_questions": max_questions,
            "output_schema": {
                "questions": [
                    {"question": "string", "intent": "why this question probes a knowledge gap"}
                ]
            },
        }
        response = self._model().invoke(
            "Return only JSON.\n" + json.dumps(prompt, ensure_ascii=False)
        )
        payload = _loads_json_object(_message_text(response))
        raw_questions = payload.get("questions", [])
        if not isinstance(raw_questions, list):
            raise ValueError("coverage question response must include questions list")
        questions: list[CoverageQuestion] = []
        for item in raw_questions[:max_questions]:
            if isinstance(item, str):
                questions.append(CoverageQuestion(question=item, intent="llm"))
            elif isinstance(item, dict) and str(item.get("question") or "").strip():
                questions.append(
                    CoverageQuestion(
                        question=str(item["question"]).strip(),
                        intent=str(item.get("intent") or "llm").strip(),
                    )
                )
        return questions


class LLMExternalAnswerer:
    """A-agent fallback that answers from model prior, not the local knowledge base.

    This is intentionally labeled as non-web. Production web search should replace
    this class with a search-backed provider such as Tavily, Bing, or SerpAPI.
    """

    def __init__(self, model: Any | None = None) -> None:
        self.model = model

    def _model(self) -> Any:
        if self.model is None:
            self.model = build_chat_model()
        return self.model

    def answer(self, *, question: str) -> AgentAnswer:
        response = self._model().invoke(
            "あなたは外部基準回答者Aです。ローカルRAGナレッジは見ません。"
            "一般知識で答え、不確かな場合は不明と明記してください。日本語で簡潔に回答してください。\n"
            f"質問: {question}"
        )
        return AgentAnswer(
            role="external",
            answer=_message_text(response).strip(),
            status="ok",
            notes="llm_prior_without_web_search",
            model=get_generation_model(),
        )


class LLMFactChecker:
    """D-agent checker that decides whether the gap should become a knowledge target."""

    def __init__(self, model: Any | None = None) -> None:
        self.model = model

    def _model(self) -> Any:
        if self.model is None:
            self.model = build_chat_model()
        return self.model

    def check(
        self,
        *,
        question: str,
        external_answer: AgentAnswer,
        knowledge_answer: AgentAnswer,
    ) -> FactCheckJudgment:
        prompt = {
            "task": (
                "You are fact-checker D. Decide whether A is a usable external answer and, if B "
                "looks weaker than A, classify WHY — do not default to 'the knowledge base is "
                "missing this'. B losing to A can mean the knowledge exists but retrieval missed "
                "it, generation ignored a retrieved chunk, ingestion split the fact across chunks, "
                "the question itself is ambiguous, A itself is not trustworthy, or the topic is "
                "simply out of this knowledge base's scope. If A has no sources, be conservative: "
                "pass only when the answer is basic and internally coherent. If you cannot decide "
                "confidently between two causes, choose needs_quarantine rather than guessing."
            ),
            "question": question,
            "external_answer_A": external_answer.model_dump(),
            "knowledge_only_answer_B": knowledge_answer.model_dump(),
            "failure_cause_options": {
                "missing_knowledge": "the knowledge base genuinely lacks this information",
                "retrieval_failure": "the information exists but the retriever did not surface it",
                "generation_failure": "a relevant chunk was retrieved but generation failed to use it",
                "chunking_failure": "the fact is split across chunks so no single chunk carries it",
                "ambiguous_question": "A and B answered different interpretations of the question",
                "invalid_A": "A's answer is unsourced, speculative, or wrong — not a valid reference",
                "out_of_scope": "the question is outside what this knowledge base is meant to cover",
                "needs_quarantine": "you cannot confidently pick one of the above",
            },
            "output_schema": {
                "external_status": "pass|fail|unclear",
                "knowledge_status": "pass|fail|abstain|unclear",
                "same_answer": "boolean",
                "failure_cause": (
                    "one of: missing_knowledge|retrieval_failure|generation_failure|"
                    "chunking_failure|ambiguous_question|invalid_A|out_of_scope|needs_quarantine "
                    "(omit or null only if B did not fail at all)"
                ),
                "missing_knowledge": "if failure_cause implies adding knowledge, what to add",
                "reason": "short reason",
            },
        }
        response = self._model().invoke(
            "Return only JSON.\n" + json.dumps(prompt, ensure_ascii=False)
        )
        payload = _loads_json_object(_message_text(response))
        return FactCheckJudgment.model_validate(payload)


def provided_external_answer(question: str, answers: dict[str, Any]) -> AgentAnswer | None:
    """Return a caller-supplied A-answer for deterministic or manual runs."""
    raw = answers.get(question)
    if raw is None:
        return None
    if isinstance(raw, AgentAnswer):
        return raw
    if isinstance(raw, str):
        return AgentAnswer(role="external", answer=raw, notes="provided")
    if isinstance(raw, dict):
        return AgentAnswer.model_validate({"role": "external", **raw})
    raise ValueError(f"invalid external answer for question: {question}")


def provided_knowledge_answer(question: str, answers: dict[str, Any]) -> AgentAnswer | None:
    """Return a caller-supplied B-answer from a subagent, prior run, or manual log."""
    raw = answers.get(question)
    if raw is None:
        return None
    if isinstance(raw, AgentAnswer):
        return raw
    if isinstance(raw, str):
        return AgentAnswer(role="knowledge", answer=raw, notes="provided")
    if isinstance(raw, dict):
        return AgentAnswer.model_validate({"role": "knowledge", **raw})
    raise ValueError(f"invalid knowledge answer for question: {question}")


def is_abstention(answer: str | None) -> bool:
    text = " ".join(str(answer or "").split())
    return not text or "記載がありません" in text or "不明" == text


def _b_failure_signals(
    *, knowledge_answer: AgentAnswer, judgment: FactCheckJudgment
) -> tuple[bool, bool, bool, list[str]]:
    b_runtime_failed = knowledge_answer.status not in {"released", "ok"}
    b_abstained = is_abstention(knowledge_answer.answer)
    b_judged_failed = judgment.knowledge_status in {"fail", "abstain"}
    reason_parts = []
    if b_runtime_failed:
        reason_parts.append(f"B status={knowledge_answer.status}")
    if b_abstained:
        reason_parts.append("B abstained")
    if b_judged_failed:
        reason_parts.append(f"D judged B as {judgment.knowledge_status}")
    return b_runtime_failed, b_abstained, b_judged_failed, reason_parts


def should_add_knowledge(
    *,
    external_answer: AgentAnswer,
    knowledge_answer: AgentAnswer,
    judgment: FactCheckJudgment,
) -> tuple[bool, str]:
    """Pre-taxonomy heuristic: "B looked weaker than A" -> add candidate.

    Kept standalone (rather than folded into `classify_coverage_item`) because it
    is also the fallback path used when a caller does not supply `failure_cause` —
    see that function's docstring for why the fallback exists and what it cannot
    distinguish (missing_knowledge vs. retrieval_failure vs. a bad question, etc).
    """
    b_runtime_failed, b_abstained, b_judged_failed, reason_parts = _b_failure_signals(
        knowledge_answer=knowledge_answer, judgment=judgment
    )
    a_passed = judgment.external_status == "pass"
    same_answer = judgment.same_answer is True
    accepted = a_passed and not same_answer and (b_runtime_failed or b_abstained or b_judged_failed)
    if not accepted:
        return False, ""
    if judgment.missing_knowledge:
        reason_parts.append(judgment.missing_knowledge)
    return True, " / ".join(reason_parts)


def classify_coverage_item(
    *,
    external_answer: AgentAnswer,
    knowledge_answer: AgentAnswer,
    judgment: FactCheckJudgment,
) -> tuple[Disposition, str]:
    """Turn a D judgment into a disposition, using the failure taxonomy when present.

    Without a `failure_cause`, "A beat B" only proves a *difference*, not that the
    knowledge base is missing something — B could have lost because retrieval
    missed an existing chunk, generation ignored a retrieved chunk, ingestion split
    the answer across chunks, the question was ambiguous, or A itself was wrong.
    Collapsing all of those into "add to knowledge base" (the old behavior) produces
    false-positive candidates for every non-knowledge failure mode.

    When `failure_cause` is present we still apply sanity checks rather than
    blindly trusting it, because a D judgment can be internally inconsistent (e.g.
    claiming `missing_knowledge` while also flagging A as unusable). Inconsistent
    combinations fall through to `needs_quarantine` instead of guessing.
    """
    b_runtime_failed, b_abstained, b_judged_failed, reason_parts = _b_failure_signals(
        knowledge_answer=knowledge_answer, judgment=judgment
    )
    b_failed = b_runtime_failed or b_abstained or b_judged_failed
    a_passed = judgment.external_status == "pass"
    same_answer = judgment.same_answer is True
    cause = judgment.failure_cause

    if cause is None:
        # Backward-compatible path: no taxonomy given, use the original heuristic.
        accepted, reason = should_add_knowledge(
            external_answer=external_answer, knowledge_answer=knowledge_answer, judgment=judgment
        )
        return ("add_candidate" if accepted else "no_gap"), reason

    if judgment.missing_knowledge:
        reason_parts.append(judgment.missing_knowledge)
    if judgment.reason:
        reason_parts.append(judgment.reason)
    reason = " / ".join(part for part in reason_parts if part) or f"D classified as {cause}"

    if cause == "needs_quarantine":
        return "quarantined", reason
    if same_answer:
        # A and B agree: whatever cause D named, there is no gap to act on.
        return "no_gap", reason
    if cause in REJECTED_CAUSES:
        # Ambiguous question / bad A / out of scope: adding knowledge would not
        # fix this, regardless of whether A nominally "passed".
        return "rejected", reason
    if cause in ADDABLE_CAUSES:
        if not a_passed:
            # A wasn't a valid reference, yet D claims a knowledge-fixable cause —
            # internally inconsistent. Do not auto-classify; a human decides.
            return "quarantined", reason
        if not b_failed:
            # D named a cause but none of B's own signals show a failure.
            # Likely a stale/copy-pasted judgment; do not act on it silently.
            return "quarantined", reason
        return "add_candidate", reason

    # Unknown cause value slipped past validation somehow (e.g. future taxonomy
    # entry from a newer D checker). Fail closed to quarantine, not silent add.
    return "quarantined", reason


def run_coverage_loop(
    *,
    revision_id: str,
    focus: str | None,
    seed_questions: list[str],
    external_answers: dict[str, Any],
    knowledge_answers: dict[str, Any],
    rounds: int,
    max_questions_per_round: int,
    question_generator: CoverageQuestionGenerator,
    external_answerer: ExternalAnswerer,
    fact_checker: FactChecker,
    knowledge_answerer: Any,
    answer_mode: AnswerMode,
) -> CoverageLoopResult:
    """Run C -> A/B -> D loop and return knowledge-gap candidates."""
    items: list[CoverageLoopItem] = []
    seen: set[str] = set()
    bounded_rounds = max(1, min(rounds, 5))
    for round_index in range(bounded_rounds):
        questions = question_generator.generate(
            focus=focus,
            seed_questions=seed_questions if round_index == 0 else [],
            previous_findings=items,
            max_questions=max_questions_per_round,
        )
        for generated in questions:
            question = " ".join(generated.question.split())
            if not question or question in seen:
                continue
            seen.add(question)
            external = provided_external_answer(question, external_answers)
            if external is None:
                external = external_answerer.answer(question=question)
            knowledge = provided_knowledge_answer(question, knowledge_answers)
            if knowledge is None:
                knowledge = knowledge_answerer(question=question, answer_mode=answer_mode)
            judgment = fact_checker.check(
                question=question,
                external_answer=external,
                knowledge_answer=knowledge,
            )
            disposition, candidate_reason = classify_coverage_item(
                external_answer=external,
                knowledge_answer=knowledge,
                judgment=judgment,
            )
            items.append(
                CoverageLoopItem(
                    question=question,
                    intent=generated.intent,
                    external_answer=external,
                    knowledge_answer=knowledge,
                    fact_check=judgment,
                    disposition=disposition,
                    add_knowledge_candidate=disposition == "add_candidate",
                    candidate_reason=candidate_reason,
                )
            )
    disposition_counts: dict[str, int] = {}
    cause_counts: dict[str, int] = {}
    for item in items:
        disposition_counts[item.disposition] = disposition_counts.get(item.disposition, 0) + 1
        if item.fact_check.failure_cause is not None:
            cause = item.fact_check.failure_cause
            cause_counts[cause] = cause_counts.get(cause, 0) + 1
    return CoverageLoopResult(
        revision_id=revision_id,
        focus=focus,
        rounds=bounded_rounds,
        total_questions=len(items),
        add_candidates=sum(1 for item in items if item.add_knowledge_candidate),
        disposition_counts=disposition_counts,
        cause_counts=cause_counts,
        items=items,
    )
