import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.rag.cli import _answer_one  # noqa: E402


class BlockedWorkbench:
    def answer_question(self, **_kwargs):
        return {
            "run_id": "run-local-only",
            "delivery_status": "blocked",
            "answer": None,
            "blocked_candidate": "candidate answer",
            "sources": [
                {
                    "rank": 1,
                    "score": 0.8,
                    "source": "sample.md",
                    "page": 0,
                    "snippet": "public evidence",
                }
            ],
        }


def test_cli_never_prints_blocked_candidate(capsys):
    _answer_one(BlockedWorkbench(), "question")

    captured = capsys.readouterr()
    assert "品質検証を通過しなかった" in captured.out
    assert "run-local-only" in captured.out
    assert "candidate answer" not in captured.out
