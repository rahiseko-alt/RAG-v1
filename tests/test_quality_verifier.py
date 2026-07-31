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
