"""Agent loop for discovering knowledge gaps before editing the knowledge base."""
from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from src.rag import AnswerMode, build_chat_model, get_generation_model


AgentRole = Literal["external", "knowledge"]
FactStatus = Literal["pass", "fail", "abstain", "unclear"]


class CoverageQuestion(BaseModel):
    """A question generated to probe causal, hierarchical, or edge-case gaps."""

    question: str = Field(..., min_length=1)
    intent: str = ""


class AgentAnswer(BaseModel):
    """One answer produced by an agent participating in the coverage loop."""

    role: AgentRole
    answer: str | None = None
    status: str = "ok"
    sources: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""
    model: str | None = None


class FactCheckJudgment(BaseModel):
    """D-agent judgment comparing external and knowledge-only answers."""

    external_status: FactStatus
    knowledge_status: FactStatus
    same_answer: bool = False
    missing_knowledge: str = ""
    reason: str = ""


class CoverageLoopItem(BaseModel):
    question: str
    intent: str = ""
    external_answer: AgentAnswer
    knowledge_answer: AgentAnswer
    fact_check: FactCheckJudgment
    add_knowledge_candidate: bool
    candidate_reason: str = ""


class CoverageLoopResult(BaseModel):
    """Aggregated result of one coverage loop run."""

    revision_id: str
    focus: str | None = None
    rounds: int
    total_questions: int
    add_candidates: int
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
                "You are fact-checker D. Decide whether A is a usable external answer and "
                "whether B failed because the local knowledge base lacks needed information. "
                "If A has no sources, be conservative: pass only when the answer is basic and internally coherent."
            ),
            "question": question,
            "external_answer_A": external_answer.model_dump(),
            "knowledge_only_answer_B": knowledge_answer.model_dump(),
            "output_schema": {
                "external_status": "pass|fail|unclear",
                "knowledge_status": "pass|fail|abstain|unclear",
                "same_answer": "boolean",
                "missing_knowledge": "what knowledge should be added if B failed",
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


def should_add_knowledge(
    *,
    external_answer: AgentAnswer,
    knowledge_answer: AgentAnswer,
    judgment: FactCheckJudgment,
) -> tuple[bool, str]:
    b_runtime_failed = knowledge_answer.status not in {"released", "ok"}
    b_abstained = is_abstention(knowledge_answer.answer)
    b_judged_failed = judgment.knowledge_status in {"fail", "abstain"}
    a_passed = judgment.external_status == "pass"
    same_answer = judgment.same_answer is True
    accepted = a_passed and not same_answer and (b_runtime_failed or b_abstained or b_judged_failed)
    if not accepted:
        return False, ""
    reason_parts = []
    if b_runtime_failed:
        reason_parts.append(f"B status={knowledge_answer.status}")
    if b_abstained:
        reason_parts.append("B abstained")
    if b_judged_failed:
        reason_parts.append(f"D judged B as {judgment.knowledge_status}")
    if judgment.missing_knowledge:
        reason_parts.append(judgment.missing_knowledge)
    return True, " / ".join(reason_parts)


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
            add_candidate, candidate_reason = should_add_knowledge(
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
                    add_knowledge_candidate=add_candidate,
                    candidate_reason=candidate_reason,
                )
            )
    return CoverageLoopResult(
        revision_id=revision_id,
        focus=focus,
        rounds=bounded_rounds,
        total_questions=len(items),
        add_candidates=sum(1 for item in items if item.add_knowledge_candidate),
        items=items,
    )
