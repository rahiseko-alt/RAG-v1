"""Agent loop for discovering knowledge gaps before editing the knowledge base."""
from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from src.rag import AnswerMode, build_chat_model, get_generation_model

# Imported rather than reimplemented so the probe splits text the same way the BM25
# retrieval lane does. A probe that tokenized differently from the retriever would
# report "the corpus does not contain this" for wording the retriever can in fact
# match, which is the exact mistake the probe exists to prevent.
from src.rag import _query_terms
from src.text_normalize import normalize_for_matching


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

# 自動採用の出典要件（設計レポート「自動採用条件案」: A の source_type が
# official / primary / reliable_secondary）。
#
# これを機械側に置く理由は、D の裁量に任せた結果が実測で割れたため。2026-08-02 の30問では、
# 出典がファンサイト単独の16件が `invalid_A` 9 対 `missing_knowledge` 7 とほぼ半々に分かれ、
# 実際の判別軸は「A 自身が原典未確認を notes に書いたか」という文書化されていないルールだった。
# 同じ品質の A が、片方は永久却下、片方はナレッジ追加候補になっていた。
#
# ここで縛るのは**採用**だけで、却下ではない。要件を満たさない A は候補にせず隔離へ送る
# （＝人が見る）。「fan 出典しか無い問いは弱点ではない」とは言えないため、捨てはしない。
ACCEPTABLE_EXTERNAL_SOURCE_TYPES: frozenset[str] = frozenset(
    {"official", "primary", "reliable_secondary"}
)


def has_acceptable_external_evidence(external_answer: AgentAnswer) -> bool:
    """True when A cites at least one source type the design doc allows auto-adoption on."""
    return any(
        str(source.source_type or "").strip().casefold() in ACCEPTABLE_EXTERNAL_SOURCE_TYPES
        for source in external_answer.evidence
    )


# Per-term id lists are examples, not the full set — see `CorpusTermEvidence`.
MAX_REPORTED_CHUNK_IDS = 10


class CoverageQuestion(BaseModel):
    """A question generated to probe causal, hierarchical, or edge-case gaps."""

    question: str = Field(..., min_length=1)
    intent: str = ""


class EvidenceSource(BaseModel):
    """One reference backing an A-answer (session-report step 2: 出典URL/出典種別/根拠span/更新日).

    Optional on `AgentAnswer` so callers that predate the taxonomy keep working, but
    no longer inert: `classify_coverage_item` reads `source_type` and refuses to turn
    a gap into an auto-adoptable candidate unless one source clears
    `ACCEPTABLE_EXTERNAL_SOURCE_TYPES`. An A-answer with no evidence therefore lands in
    quarantine rather than in the candidate list.
    """

    url: str = ""
    source_type: str = ""
    span: str = ""
    updated_at: str = ""


class RetrievedChunk(BaseModel):
    """One chunk B's retriever surfaced (session-report step 2/7: 取得chunk/score/引用箇所/検索順位).

    Recording this per-answer is what lets a D judgment separate failure modes that
    B's answer text alone cannot. Note what it does and does not prove:

    - Positively: a surfaced chunk already carried the needed fact and B still
      failed -> `generation_failure`.
    - Not provable from here: the fact being absent from the surfaced chunks is
      equally consistent with `missing_knowledge` (the corpus lacks it),
      `retrieval_failure` (the corpus has it, retrieval did not surface it), and
      `chunking_failure` (it is split across chunks). Separating those needs corpus
      evidence, not this list — see `LLMFactChecker`'s prompt.

    Field meanings, so that externally injected B-answers (subagent output, a
    prior run's log) record the same thing our own runtime does:

    - `chunk_id`  : the retriever's identifier for the chunk (opaque key).
    - `score`     : the retrieval score, higher = closer match.
    - `citation`  : human-readable *where it came from*, e.g. `"sample.md p.3"`.
      Deliberately a locator and not the passage text — the passage already
      travels in `AgentAnswer.sources[*].snippet`, and duplicating it here would
      double the size of every persisted ledger row.
    - `rank`      : 1-based retrieval order. This is also the number used by the
      `[N]` citation markers in B's answer text (see `verifier.CITATION_PATTERN`),
      so D can check whether B actually cited the chunk that carried the answer.
    """

    chunk_id: str = ""
    score: float | None = None
    citation: str = ""
    rank: int | None = None


CORPUS_PROBE_METHOD = (
    "lexical term presence across the whole corpus, tokenized the same way the BM25 "
    "retrieval lane tokenizes. IT CANNOT SEE PARAPHRASE: a term reported absent means "
    "no chunk contains that exact string, NOT that the corpus is silent on the idea — "
    "the corpus may state the same thing in different words. Presence is weaker still: "
    "a corpus can name an entity without carrying the causal link, condition, or "
    "exception a question asks about. Never conclude missing_knowledge from absent "
    "terms alone; check the surfaced chunk text first."
)


class CorpusChunk(BaseModel):
    """One chunk of the *entire* corpus — including chunks retrieval never surfaced."""

    chunk_id: str = ""
    text: str = ""


class CorpusTermEvidence(BaseModel):
    """Where one probed term occurs corpus-wide, and whether B's retriever surfaced it.

    `source` says where the term came from. It is reported rather than filtered on: an
    earlier draft probed only the terms A introduced that the question did not already
    use, on the theory that the question's own vocabulary tells D nothing new. That is
    wrong in the decisive case — for "黒閃を経験した術師が…なぜですか", the single most
    informative finding is that the corpus never says 黒閃 at all, and filtering by the
    question would have dropped exactly that term. Extra terms only cost D some reading;
    a dropped term costs a cause it could have named.
    """

    term: str
    source: Literal["question", "external_answer", "both"] = "external_answer"
    # Counts are exact; the id lists are capped at `MAX_REPORTED_CHUNK_IDS`. A common
    # term hits most of the corpus, and this model is persisted per candidate — on a
    # 40k-chunk corpus the uncapped lists would put megabytes into a single ledger row,
    # reintroducing by id the corpus duplication that keeping chunk text out avoids.
    # What the judge needs is whether a term is anywhere, whether it is in what B saw,
    # and a few examples; the exact id set is reproducible from the corpus.
    corpus_hit_count: int = 0
    surfaced_hit_count: int = 0
    corpus_chunk_ids: list[str] = Field(default_factory=list)
    surfaced_chunk_ids: list[str] = Field(default_factory=list)


class CorpusProbe(BaseModel):
    """Corpus-side evidence that lets D separate causes B's own output cannot.

    `RetrievedChunk` deliberately proves only one direction: a surfaced chunk carried
    the fact and B still failed -> `generation_failure`. The reverse — the fact not
    being in the surfaced chunks — is equally consistent with `missing_knowledge`,
    `retrieval_failure`, and `chunking_failure`, and D is not given the corpus, so it
    cannot choose between them. The 2026-08-02 30-question run is what that costs:
    24 of 30 items landed on `needs_quarantine`, which is correct behavior and also
    useless as a weakness table.

    This probe supplies the missing side. It takes the terms A's answer introduced and
    reports, for each, which corpus chunks contain it and which of those B's retriever
    actually surfaced. That yields three positively-assertable readings:

    - `absent_from_corpus`      -> no chunk contains that exact string, so
                                   `missing_knowledge` is assertable *only after* the
                                   surfaced chunk text has been checked (see below).
    - `present_but_unsurfaced`  -> the corpus has it and retrieval did not return it,
                                   so `retrieval_failure` is assertable.
    - `present_in_surfaced`     -> B was handed the wording and still failed, which is
                                   `generation_failure` territory; if the terms are
                                   spread across several surfaced chunks with no single
                                   chunk carrying them together, that is the
                                   `chunking_failure` signature.

    What it deliberately does NOT do is decide. `classify_coverage_item` gained no new
    gate from this: term presence is not fact presence, so a probe showing every term
    present cannot refute a genuine `missing_knowledge` call about a causal bridge that
    the corpus never draws between terms it does mention. Narrowing D's options on
    lexical grounds would reintroduce exactly the kind of unsupported leap the taxonomy
    exists to prevent. The probe widens what D can assert; D still judges.

    Measured failure mode, from adversarial verification of the 2026-08-02 re-run (see
    `docs/session-reports/2026-08-02-coverage-loop-30q-run.md`): with the probe added
    but D still limited to 240-character snippets, 2 of 8 spot-checked judgments were
    wrong, because the sentence answering the question sat inside a surfaced chunk,
    worded differently from A. One of those two was a regression from a correct
    `generation_failure`; the other had been `needs_quarantine`, so the probe also
    turned a withheld judgment into a confident wrong one. Across the whole run both
    `generation_failure` items the pre-probe run had found stopped being
    `generation_failure` — the count went 2 to 0. Absent terms therefore rank *below*
    the surfaced chunk text, never above it, which is what `AgentAnswer.surfaced_texts`
    exists to make possible.
    """

    method: str = CORPUS_PROBE_METHOD
    corpus_chunk_count: int = 0
    surfaced_chunk_count: int = 0
    # `candidate_term_count` counts the terms worth probing; `probed_terms` holds the
    # ones actually looked up. When `truncated` is true the two differ and "absent from
    # `absent_from_corpus`" stops meaning "present in the corpus".
    candidate_term_count: int = 0
    truncated: bool = False
    probed_terms: list[CorpusTermEvidence] = Field(default_factory=list)
    absent_from_corpus: list[str] = Field(default_factory=list)
    present_but_unsurfaced: list[str] = Field(default_factory=list)
    present_in_surfaced: list[str] = Field(default_factory=list)


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
    # Full text of the chunks B was handed, for the judge only. `sources[*].snippet` is
    # truncated to 240 characters, which is enough to show *that* a chunk was retrieved
    # and not enough to show whether it answered the question: in the 2026-08-02 re-run,
    # both misclassified items had their answer inside a surfaced chunk past the 240th
    # character, so D could not see the one thing the retrieval evidence positively
    # proves. This field is stripped before the loop result is persisted or returned —
    # see `QualityWorkbench.run_revision_coverage_loop` — because the ledger and the API
    # response must not carry a second copy of the corpus.
    surfaced_texts: list[dict[str, Any]] = Field(default_factory=list)
    # Set on the role="knowledge" answer only. It lives here rather than on the item so
    # it reaches D and the ledger through the paths that already carry B's retrieval
    # evidence (the D prompt dumps this model; the ledger persists it as
    # `knowledge_answer_json`), with no schema migration. The terms it probes come from
    # A's answer — it is evidence about B's retrieval, queried with A's vocabulary.
    corpus_probe: CorpusProbe | None = None


class FactCheckJudgment(BaseModel):
    """D-agent judgment comparing external and knowledge-only answers.

    `failure_cause` is optional so existing callers (fixtures, manually supplied
    fact_checks) that predate the taxonomy keep working: when it is omitted, the
    loop falls back to the pre-taxonomy heuristic (see `classify_coverage_item`).
    Once a caller supplies it, D is treated as authoritative for *why* B lost to A,
    not just *whether* it did — this is what prevents "B looked weaker than A"
    from being auto-translated into "add to knowledge base" for every gap.

    Measured accuracy against a construction-verified gold set (2026-08-03, see
    `docs/session-reports/2026-08-03-classifier-accuracy.md` and
    `data/eval/classifier-gold-set-v1.json`), 22/27 (81.5%) overall — above RAGEC's
    reported human-agreement baselines for comparable stage classification (57.8%)
    and error-type accuracy (40.3%). That aggregate hides a real per-label split:
    `missing_knowledge`/`retrieval_failure`/`generation_failure`/`invalid_A` were each
    100% (22/22 combined) on this gold set, but every `chunking_failure` gold item
    (0/5) was instead judged `generation_failure` — consistently and, per the
    judgments' own reasoning, correctly by the taxonomy's own definition: each
    gold item turned out to have BOTH halves of its compound question answerable
    from a single already-complete chunk, which is a generation/synthesis miss, not
    ingest fragmenting one fact. That gold-set construction method (built from
    multi-hop "combine 2 chunks" eval items) does not actually test `chunking_failure`
    as defined — this taxonomy's `chunking_failure` label therefore remains UNVALIDATED
    and should not drive auto-promotion (see Phase 2) until a gold set that genuinely
    fragments a fact mid-chunk exists. `needs_quarantine`/`ambiguous_question`/
    `out_of_scope` are also unmeasured (no gold-set coverage yet).
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


_KANA_ONLY = re.compile(r"^[ぁ-ゟ゠-ヿー]+$")


def is_probeable_term(term: str) -> bool:
    """Reject tokens whose absence from a corpus would mean nothing.

    `_query_terms` is the retriever's tokenizer: it splits on particles and keeps
    whatever falls out. For retrieval a junk token is harmless — it just fails to
    match. Here the sign flips, because absence is handed to D as evidence, so junk
    manufactures evidence. The 2026-08-02 run measured 725 of 1200 probed tokens as
    absent, among them `つ完璧` (「かつ完璧」split at 「か」), `黒く光る現象`, and bare
    inflections like `った` / `され` — none of which any corpus would contain verbatim.

    Two rejections, both structural rather than domain-specific:

    - kana-only tokens, which are inflections and particles, never domain terms. A
      real Japanese term carries kanji, latin, or digits.
    - single characters, which match almost everywhere and prove nothing either way.

    Multi-morpheme phrases are deliberately NOT rejected here: some are real terms
    (`反転術式`), and a length rule cannot tell those from split fragments. The D
    prompt handles that by telling the judge that an absent long phrase is expected
    and is not evidence.
    """
    stripped = term.strip()
    if len(stripped) < 2:
        return False
    return not _KANA_ONLY.match(stripped)


class CorpusProber(Protocol):
    def probe(
        self,
        *,
        question: str,
        external_answer: AgentAnswer,
        knowledge_answer: AgentAnswer,
    ) -> CorpusProbe | None:
        """Locate A's terms in the whole corpus, relative to what B surfaced."""

    def surfaced_texts(self, *, knowledge_answer: AgentAnswer) -> list[dict[str, Any]]:
        """Full text of the chunks B retrieved, for the judge (see `surfaced_texts`)."""


def build_corpus_probe(
    *,
    question: str,
    external_answer: AgentAnswer,
    knowledge_answer: AgentAnswer,
    corpus: list[CorpusChunk],
    max_terms: int = 40,
) -> CorpusProbe:
    """Build the corpus-side evidence described in `CorpusProbe`.

    Deterministic and model-free on purpose: it must run on fully injected loops
    (`allow_llm_agents=false`), which is how this workbench is meant to be driven, and
    a probe that needed an API key would be absent exactly when it is most needed.
    """
    question_terms = [term for term in _query_terms(question) if is_probeable_term(term)]
    answer_terms = [
        term
        for term in _query_terms(str(external_answer.answer or ""))
        if is_probeable_term(term)
    ]
    question_term_set = set(question_terms)
    answer_term_set = set(answer_terms)
    # Question-only terms go FIRST, not last. They used to be appended after A's terms
    # and then cut by `max_terms`, which silently deleted every one of them in all 30
    # questions of the 2026-08-02 run (measured `source` split: external_answer 1121,
    # both 79, question 0) — exactly the decisive term `CorpusTermEvidence.source` was
    # added to protect. Ordering them ahead of A's terms is what actually protects them.
    question_only = [term for term in question_terms if term not in answer_term_set]
    terms = question_only + answer_terms
    probed_terms_limit = min(len(terms), max_terms)

    surfaced_ids = {
        str(chunk.chunk_id)
        for chunk in knowledge_answer.retrieved_chunks
        if str(chunk.chunk_id)
    }
    normalized_corpus = [
        (chunk.chunk_id, normalize_for_matching(chunk.text)) for chunk in corpus
    ]

    probed: list[CorpusTermEvidence] = []
    absent: list[str] = []
    unsurfaced: list[str] = []
    surfaced: list[str] = []
    for term in terms[:probed_terms_limit]:
        corpus_hits = [chunk_id for chunk_id, text in normalized_corpus if term in text]
        surfaced_hits = [chunk_id for chunk_id in corpus_hits if chunk_id in surfaced_ids]
        in_question = term in question_term_set
        in_answer = term in answer_term_set
        probed.append(
            CorpusTermEvidence(
                term=term,
                source=(
                    "both" if in_question and in_answer
                    else "question" if in_question
                    else "external_answer"
                ),
                corpus_hit_count=len(corpus_hits),
                surfaced_hit_count=len(surfaced_hits),
                corpus_chunk_ids=corpus_hits[:MAX_REPORTED_CHUNK_IDS],
                surfaced_chunk_ids=surfaced_hits[:MAX_REPORTED_CHUNK_IDS],
            )
        )
        if not corpus_hits:
            absent.append(term)
        elif surfaced_hits:
            surfaced.append(term)
        else:
            unsurfaced.append(term)

    return CorpusProbe(
        corpus_chunk_count=len(corpus),
        surfaced_chunk_count=len(surfaced_ids),
        probed_terms=probed,
        absent_from_corpus=absent,
        present_but_unsurfaced=unsurfaced,
        present_in_surfaced=surfaced,
        candidate_term_count=len(terms),
        # Reported, not just applied: a truncated probe that does not say so invites the
        # reader to treat "not in absent_from_corpus" as "present in the corpus", when
        # the term may simply never have been looked up.
        truncated=len(terms) > probed_terms_limit,
    )


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


def build_fact_check_prompt(
    *,
    question: str,
    external_answer: AgentAnswer,
    knowledge_answer: AgentAnswer,
) -> dict[str, Any]:
    """Build the exact prompt dict `LLMFactChecker.check` sends to the model.

    Pulled out as a standalone function so anything that needs to reproduce D's
    input byte-for-byte (e.g. `scripts/prepare_classifier_gold_prompts.py`, which
    hands the same prompt to a subagent standing in for D when no API key is
    available) imports it instead of re-copying a prompt that would then drift
    from production, the same risk `scripts/coverage_loop_retrieve.py` documents
    for its own copy of the retrieve node.
    """
    return {
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
            "how_to_use_B_retrieval_evidence": (
                "B's `retrieved_chunks` and `sources` show what its retriever actually surfaced. "
                "They prove one thing positively: if a surfaced chunk already carries the needed "
                "fact and B still failed, that is generation_failure. They do NOT prove the "
                "reverse. The fact being absent from the surfaced chunks is equally consistent "
                "with missing_knowledge (the corpus lacks it), retrieval_failure (the corpus has "
                "it but retrieval did not surface it), and chunking_failure (it is split so no "
                "single chunk carries it). B's own evidence cannot tell those three apart — for "
                "that you need the corpus probe below, and without it use needs_quarantine."
            ),
            "evidence_order_you_must_follow": (
                "1. Read `knowledge_only_answer_B.surfaced_texts` — the FULL text of the "
                "chunks B was handed. If one of them answers the question, in any wording, "
                "the cause is generation_failure and you are done. If several of them "
                "together answer it but no single one does, the cause is chunking_failure. "
                "If they answer only part of what was asked, do NOT stop here: say so, and "
                "carry the unanswered part to step 2. Do not skip this step — in a measured "
                "run, every judgment that got the cause wrong got it wrong here, by treating "
                "a chunk as silent because A's exact words were not in it. "
                "`sources[*].snippet` is truncated at 240 characters, so never treat a "
                "snippet as the whole chunk. If `surfaced_texts` is empty, or entries carry "
                "`unresolved: true`, then step 1 COULD NOT BE PERFORMED for those chunks: "
                "you have not seen what B saw, so you may not conclude anything from their "
                "silence — answer needs_quarantine unless the resolved chunks alone settle "
                "it. 2. Only for the part step 1 left open may you use `corpus_probe`, and "
                "only to choose between missing_knowledge and retrieval_failure. 3. If "
                "neither step decides it, use needs_quarantine."
            ),
            "how_to_use_the_corpus_probe": (
                "`knowledge_only_answer_B.corpus_probe`, when present, supplies the corpus-side "
                "evidence B's own output lacks. It takes the terms A's answer introduced and "
                "reports where each occurs across the WHOLE corpus, including chunks retrieval "
                "never returned. It matches exact strings and CANNOT SEE PARAPHRASE, so a term "
                "in `absent_from_corpus` means the corpus never uses that wording — not that "
                "the corpus is silent on the idea. Use it only after step 1 above has ruled "
                "out generation_failure and chunking_failure. Terms in `absent_from_corpus` "
                "then support missing_knowledge, and terms in `present_but_unsurfaced` "
                "support retrieval_failure. Read absence critically rather than as proof: "
                "the tokenizer splits on particles, so the list also holds sentence "
                "fragments and multi-word phrases that no corpus would ever contain "
                "verbatim. A long phrase being absent is expected and is NOT evidence — "
                "only a short domain term counts, and only if it bears on the disputed "
                "fact. If `truncated` is true, terms beyond `probed_terms` were never "
                "looked up at all, so a term's absence from these lists says nothing. "
                "Presence is weaker still: a corpus can name every entity in A's answer "
                "and still never draw the causal link, condition, or exception the "
                "question asks for — that is still missing_knowledge. When the probe is "
                "absent, or its terms do not bear on the disputed fact, choose "
                "needs_quarantine rather than guessing."
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
        prompt = build_fact_check_prompt(
            question=question,
            external_answer=external_answer,
            knowledge_answer=knowledge_answer,
        )
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
        if not has_acceptable_external_evidence(external_answer):
            # The design doc's auto-adoption condition, enforced here rather than left
            # to D — see `ACCEPTABLE_EXTERNAL_SOURCE_TYPES`. Quarantine, not reject:
            # the gap may well be real, it just cannot be adopted on this sourcing.
            return "quarantined", " / ".join(
                part
                for part in (
                    reason,
                    "A has no official/primary/reliable_secondary source — not auto-adoptable",
                )
                if part
            )
        return "add_candidate", reason

    # Unknown cause value slipped past validation somehow (e.g. future taxonomy
    # entry from a newer D checker). Fail closed to quarantine, not silent add.
    return "quarantined", reason


# Causes that may drive the ledger's implemented -> verified -> active pipeline
# (`WorkbenchStore.verify_coverage_candidate`) fully automatically, without a per-item
# human click. `chunking_failure` is deliberately excluded: the 2026-08-03 gold-set
# measurement (`docs/session-reports/2026-08-03-classifier-accuracy.md`) found it 0/5 —
# every gold `chunking_failure` item was instead judged `generation_failure`, because
# the gold-set construction method tested multi-hop synthesis rather than true
# mid-chunk fact fragmentation. That is a gold-set defect, not proof the classifier is
# wrong about real chunking_failure cases, but it means this cause has never been
# checked against a known-correct answer and must not silently auto-promote.
AUTO_PROMOTABLE_CAUSES: frozenset[str] = ADDABLE_CAUSES - {"chunking_failure"}


def is_promotion_eligible(
    *, failure_cause: str | None, validation_result: dict[str, Any]
) -> tuple[bool, str]:
    """Design-doc auto-adoption gate, applied to an *implemented* candidate.

    `classify_coverage_item` already enforces the sourcing bar (`has_acceptable_
    external_evidence`) at classification time. This is the second half — "改善確認・
    既存PASS非悪化" (improvement confirmed, no existing PASS regressed) — read from a
    validation-job result for the revision built from the candidate, plus the
    `AUTO_PROMOTABLE_CAUSES` gate above. Kept outside `classify_coverage_item` on
    purpose: that function decides disposition at classification time, before any
    revision or validation job exists; this decides promotion afterward, against
    evidence that did not exist yet when D first judged the item.
    """
    if failure_cause not in AUTO_PROMOTABLE_CAUSES:
        return False, f"failure_cause {failure_cause!r} is not eligible for auto-promotion"
    if validation_result.get("full_pass") is not True:
        return False, "validation job did not reach full_pass"
    if validation_result.get("no_regression") is not True:
        return False, "validation job reported a regression against the active revision"
    return True, "validation job confirmed improvement with no regression"


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
    corpus_prober: CorpusProber | None = None,
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
            if corpus_prober is not None:
                # Always overwritten, never merged with what a caller supplied. Both
                # fields are derived from the corpus, and `surfaced_texts` is now the
                # channel that ends the judgment at step 1 — the most authoritative one
                # there is. Honouring a caller-supplied value would let an injected
                # B-answer hand D fabricated chunk text, which is the exact prohibition
                # in memory.md: never put evidence in front of D that was not produced.
                probe = corpus_prober.probe(
                    question=question,
                    external_answer=external,
                    knowledge_answer=knowledge,
                )
                knowledge = knowledge.model_copy(
                    update={
                        "corpus_probe": probe,
                        "surfaced_texts": corpus_prober.surfaced_texts(
                            knowledge_answer=knowledge
                        ),
                    }
                )
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
