import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.quality import OnlineVerifier, public_verification  # noqa: E402


EVIDENCE = [
    {
        "rank": 1,
        "source": "sample.md",
        "page": 0,
        "text": "The evidence supports the answer.",
    }
]


class StaticVerifier:
    def __init__(self, *, status="supported", axes=2):
        self.status = status
        self.axes = axes

    def verify(self, **_kwargs):
        return {
            "faithfulness": self.axes,
            "relevance": self.axes,
            "no_misinfo": self.axes,
            "claims": [
                {
                    "claim_id": "c1",
                    "claim": "Supported answer.",
                    "status": self.status,
                    "evidence": [{"rank": 1, "reason": "private model explanation"}],
                    "reason": "private model explanation",
                }
            ],
        }


class RaisingVerifier:
    def verify(self, **_kwargs):
        raise RuntimeError("provider failed")


def test_release_requires_all_deterministic_semantic_checks():
    verifier = OnlineVerifier(StaticVerifier(), timeout_seconds=1)

    result = verifier.evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=EVIDENCE,
    )

    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert result["deterministic"]["all_pass"] is True
    assert all(score == 2 for score in result["axes"].values())


def test_invalid_citation_blocks_even_when_llm_scores_two():
    verifier = OnlineVerifier(StaticVerifier(), timeout_seconds=1)

    result = verifier.evaluate(
        question="What?",
        candidate_answer="Unsupported citation [9].",
        evidence=EVIDENCE,
    )

    assert result["status"] == "block"
    assert result["release_allowed"] is False
    assert result["deterministic"]["all_pass"] is False


def test_verifier_cannot_omit_a_candidate_sentence():
    verifier = OnlineVerifier(StaticVerifier(), timeout_seconds=1)

    result = verifier.evaluate(
        question="What?",
        candidate_answer="Supported answer [1]. Omitted claim [1].",
        evidence=EVIDENCE,
    )

    assert result["claim_coverage"] is False
    assert result["release_allowed"] is False


def test_note_prefix_does_not_bypass_verification():
    verifier = OnlineVerifier(StaticVerifier(), timeout_seconds=1)

    result = verifier.evaluate(
        question="What?",
        candidate_answer="Supported answer [1]. ※Unsupported note.",
        evidence=EVIDENCE,
    )

    assert result["deterministic"]["all_pass"] is False
    assert result["claim_coverage"] is False
    assert result["release_allowed"] is False


def test_decimal_point_does_not_break_citation_coverage():
    class DecimalVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "decimal",
                        "claim": "Dose is 2.5 mg.",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    result = OnlineVerifier(DecimalVerifier(), timeout_seconds=1).evaluate(
        question="Dose?",
        candidate_answer="Dose is 2.5 mg [1].",
        evidence=EVIDENCE,
    )

    assert result["deterministic"]["all_pass"] is True
    assert result["release_allowed"] is True


def test_japanese_sentence_punctuation_does_not_break_claim_coverage():
    class JapaneseVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "author",
                        "claim": "『呪術廻戦』の作者は芥見下々です",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    result = OnlineVerifier(JapaneseVerifier(), timeout_seconds=1).evaluate(
        question="作者は？",
        candidate_answer="『呪術廻戦』の作者は芥見下々です [1]。",
        evidence=EVIDENCE,
    )

    assert result["claim_coverage"] is True
    assert result["release_allowed"] is True


def test_unknown_claim_blocks_and_public_result_removes_private_reason():
    verifier = OnlineVerifier(StaticVerifier(status="unclear"), timeout_seconds=1)
    result = verifier.evaluate(
        question="What?",
        candidate_answer="Unclear answer [1].",
        evidence=EVIDENCE,
    )

    public = public_verification(result, delivery_status="blocked")

    assert result["status"] == "block"
    assert public["claims"][0]["status"] == "unclear"
    assert public["claims"][0]["claim_id"] == "blocked-claim-1"
    assert "private model explanation" not in str(public)


def test_standard_mode_can_release_supported_answer_without_strict_claim_coverage():
    class LooseCoverageVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 1,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "Supported answer",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    strict = OnlineVerifier(LooseCoverageVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer with extra wording [1].",
        evidence=EVIDENCE,
        answer_mode="strict",
    )
    standard = OnlineVerifier(LooseCoverageVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer with extra wording [1].",
        evidence=EVIDENCE,
        answer_mode="standard",
    )

    assert strict["release_allowed"] is False
    assert strict["reason_code"] == "quality_gate_failed"
    assert standard["release_allowed"] is True
    assert standard["answer_mode"] == "standard"


def test_standard_mode_still_blocks_contradictions():
    class ContradictingVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "Supported answer.",
                        "status": "contradicted",
                        "evidence": [{"rank": 1, "reason": "contradicted"}],
                        "reason": "contradicted",
                    }
                ],
            }

    result = OnlineVerifier(ContradictingVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=EVIDENCE,
        answer_mode="standard",
    )

    assert result["release_allowed"] is False
    assert result["reason_code"] == "standard_gate_failed"


def test_verifier_error_fails_closed():
    verifier = OnlineVerifier(RaisingVerifier(), timeout_seconds=1)

    result = verifier.evaluate(
        question="What?",
        candidate_answer="Answer [1].",
        evidence=EVIDENCE,
    )

    assert result["status"] == "error"
    assert result["reason_code"] == "verifier_error"
    assert result["release_allowed"] is False


def test_exact_canonical_abstention_can_release_without_citation():
    class AbstentionVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "abstention",
                        "claim": "提供された文書には記載がありません。",
                        "status": "supported",
                        "evidence": [],
                        "reason": "the retrieved evidence does not answer the question",
                    }
                ],
            }

    result = OnlineVerifier(AbstentionVerifier(), timeout_seconds=1).evaluate(
        question="文書に存在しない情報は？",
        candidate_answer="提供された文書には記載がありません。",
        evidence=EVIDENCE,
    )

    assert result["answer_type"] == "abstention"
    assert result["deterministic"]["all_pass"] is True
    assert result["claim_evidence_valid"] is True
    assert result["release_allowed"] is True


def test_arbitrary_uncited_answer_still_blocks():
    result = OnlineVerifier(StaticVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer.",
        evidence=EVIDENCE,
    )

    assert result["deterministic"]["all_pass"] is False
    assert result["release_allowed"] is False


def test_multiple_verified_claims_can_cover_one_compound_sentence():
    class CompoundVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "techniques",
                        "claim": "虎杖悠仁は御廚子と赤血操術を使用します。",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    },
                ],
            }

    result = OnlineVerifier(CompoundVerifier(), timeout_seconds=1).evaluate(
        question="虎杖悠仁の術式は？",
        candidate_answer="虎杖悠仁は御廚子と赤血操術を使用します [1]。",
        evidence=EVIDENCE,
    )

    assert result["claim_coverage"] is True
    assert result["release_allowed"] is True


def test_fragmented_claims_cannot_collectively_cover_candidate():
    class FragmentedVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "fragment-1",
                        "claim": "abcdef",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    },
                    {
                        "claim_id": "fragment-2",
                        "claim": "efghij",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    },
                ],
            }

    result = OnlineVerifier(FragmentedVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="abcdefghij [1].",
        evidence=EVIDENCE,
    )

    assert result["claim_coverage"] is False
    assert result["release_allowed"] is False


def test_fan_only_evidence_cannot_be_presented_as_official_fact():
    evidence = [{**EVIDENCE[0], "authority": "fan"}]
    result = OnlineVerifier(StaticVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=evidence,
    )

    assert result["authority_alignment"] is False
    assert result["release_allowed"] is False


def test_fan_only_evidence_can_release_when_labeled_as_fan_interpretation():
    class FanVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "fan-claim",
                        "claim": "ファンの考察ではこの解釈が支持されています。",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    result = OnlineVerifier(FanVerifier(), timeout_seconds=1).evaluate(
        question="ファンの見方は？",
        candidate_answer="ファンの考察ではこの解釈が支持されています [1]。",
        evidence=[{**EVIDENCE[0], "authority": "fan"}],
    )

    assert result["authority_alignment"] is True
    assert result["release_allowed"] is True


def test_word_explanation_does_not_count_as_fan_theory_label():
    class ExplanationVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "claim",
                        "claim": "これは公式設定の説明です。",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    result = OnlineVerifier(ExplanationVerifier(), timeout_seconds=1).evaluate(
        question="公式設定ですか？",
        candidate_answer="これは公式設定の説明です [1]。",
        evidence=[{**EVIDENCE[0], "authority": "fan"}],
    )

    assert result["authority_alignment"] is False
    assert result["release_allowed"] is False


def test_word_novel_does_not_count_as_fan_theory_label():
    class NovelVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "claim",
                        "claim": "この小説が原作です。",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    result = OnlineVerifier(NovelVerifier(), timeout_seconds=1).evaluate(
        question="原作は？",
        candidate_answer="この小説が原作です [1]。",
        evidence=[{**EVIDENCE[0], "authority": "fan"}],
    )

    assert result["authority_alignment"] is False
    assert result["release_allowed"] is False


# --- 部分回答＋部分留保 -------------------------------------------------------
#
# The 2026-08-02 30-question run measured every B-answer taking the shape "answer what
# the excerpts support, then mark the rest undetermined", and 25 of 30 were blocked —
# all of them on citation_coverage, because the closing sentence has nothing to cite.
# The gate was rejecting the most careful answers the system produces. These pin the
# narrow exemption that fixes it, and its limits.

def test_partial_answer_with_canonical_reservation_can_release():
    class PartialVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "抜粋は結論を支持しています [1]。",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "private model explanation"}],
                        "reason": "private model explanation",
                    }
                ],
            }

    result = OnlineVerifier(PartialVerifier(), timeout_seconds=1).evaluate(
        question="仕組みを説明してください。",
        candidate_answer=(
            "抜粋は結論を支持しています [1]。"
            "その仕組みの詳細は、提供された抜粋からは特定できません。"
        ),
        evidence=EVIDENCE,
    )

    assert result["deterministic"]["all_pass"] is True
    assert result["release_allowed"] is True


# --- explore モードの合格経路（偽検証器で鍵を入れれば通ることを証明） -------------
#
# strict/standard は上の test_standard_mode_can_release_supported_answer_without_strict_claim_coverage
# 等で比較済みだが、explore モード単体の合格・不合格経路はこれまでテストが無かった
# （Phase 3 完了条件：3モードそれぞれの合格経路テストを緑にする）。


def test_explore_mode_passes_without_authority_alignment():
    """Unlike strict/standard, explore's safety requirement drops
    authority_alignment. The claim text deliberately does NOT match
    FAN_QUALIFIER_PATTERN (same non-qualifying wording as
    test_word_explanation_does_not_count_as_fan_theory_label), so
    authority_alignment genuinely computes False here — proving explore
    ignores it, not just that this fixture never exercises it."""

    class UnqualifiedFanVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "これは公式設定の説明です。",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    fan_evidence = [{**EVIDENCE[0], "authority": "fan"}]
    explore = OnlineVerifier(UnqualifiedFanVerifier(), timeout_seconds=1).evaluate(
        question="公式設定ですか？",
        candidate_answer="これは公式設定の説明です [1]。",
        evidence=fan_evidence,
        answer_mode="explore",
    )
    standard = OnlineVerifier(UnqualifiedFanVerifier(), timeout_seconds=1).evaluate(
        question="公式設定ですか？",
        candidate_answer="これは公式設定の説明です [1]。",
        evidence=fan_evidence,
        answer_mode="standard",
    )

    assert explore["authority_alignment"] is False
    assert explore["release_allowed"] is True
    # standard requires authority_alignment as part of safety_checks, so the
    # same unaligned-authority answer that passes explore must block standard.
    assert standard["authority_alignment"] is False
    assert standard["release_allowed"] is False


def test_explore_mode_passes_with_low_axis_scores():
    """explore is the loosest gate on the numeric axes: no faithfulness/
    relevance floor, only no_misinfo>=1 — proven against standard, which
    requires faithfulness>=2 and no_misinfo>=2."""

    class LooseExploreVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 0,
                "relevance": 0,
                "no_misinfo": 1,
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "Supported answer.",
                        "status": "supported",
                        "evidence": [{"rank": 1, "reason": "supported"}],
                        "reason": "supported",
                    }
                ],
            }

    explore = OnlineVerifier(LooseExploreVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=EVIDENCE,
        answer_mode="explore",
    )
    standard = OnlineVerifier(LooseExploreVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=EVIDENCE,
        answer_mode="standard",
    )

    assert explore["release_allowed"] is True
    assert explore["answer_mode"] == "explore"
    # standard requires faithfulness>=2 and no_misinfo>=2, so the same loose axes
    # that pass explore must still block standard — proving explore is genuinely
    # looser, not that the deterministic checks were accidentally skipped.
    assert standard["release_allowed"] is False
    assert standard["reason_code"] == "standard_gate_failed"


def test_explore_mode_still_blocks_contradictions_and_low_no_misinfo():
    class ContradictingVerifier:
        def verify(self, **_kwargs):
            return {
                "faithfulness": 2,
                "relevance": 2,
                "no_misinfo": 2,
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "Supported answer.",
                        "status": "contradicted",
                        "evidence": [{"rank": 1, "reason": "contradicted"}],
                        "reason": "contradicted",
                    }
                ],
            }

    contradicted = OnlineVerifier(ContradictingVerifier(), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=EVIDENCE,
        answer_mode="explore",
    )

    assert contradicted["release_allowed"] is False
    assert contradicted["reason_code"] == "explore_gate_failed"

    low_no_misinfo = OnlineVerifier(StaticVerifier(axes=0), timeout_seconds=1).evaluate(
        question="What?",
        candidate_answer="Supported answer [1].",
        evidence=EVIDENCE,
        answer_mode="explore",
    )

    assert low_no_misinfo["release_allowed"] is False
    assert low_no_misinfo["reason_code"] == "explore_gate_failed"


def test_reservation_exemption_does_not_cover_an_uncited_assertion():
    """The exemption is for sentences that assert nothing. A plain uncited claim in the
    same answer must still block, or the phrase becomes a way to skip citations."""
    result = OnlineVerifier(StaticVerifier(), timeout_seconds=1).evaluate(
        question="仕組みを説明してください。",
        candidate_answer=(
            "Supported answer."
            "詳細は、提供された抜粋からは特定できません。"
            "五条悟は最強である。"
        ),
        evidence=EVIDENCE,
    )

    assert result["deterministic"]["all_pass"] is False
    assert result["release_allowed"] is False
