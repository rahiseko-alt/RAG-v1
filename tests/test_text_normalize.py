"""表記ゆれ正規化（`src/text_normalize`）のテスト。

医療ドメインでは `5mg` と `5 mg`、`COVID-19` と `ＣＯＶＩＤ-19` が別物になると
検索が黙って落ちる。ここはその等価性を固定する。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from src.text_normalize import expand_iteration_marks, normalize_for_matching  # noqa: E402


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # 数値と単位の間の空白
        ("5mg", "5 mg"),
        ("500mg", "500　mg"),  # 全角スペース
        # 全角と半角
        ("COVID-19", "ＣＯＶＩＤ-19"),
        ("5mg", "５ｍｇ"),
        # 大文字と小文字
        ("COVID-19", "covid-19"),
        # 繰り返し記号
        ("日日", "日々"),
        # 上記の組み合わせ
        ("covid-19", "ＣＯＶＩＤ-１９"),
        ("5mg", "５ mg"),
    ],
)
def test_variants_normalize_to_the_same_form(left, right):
    assert normalize_for_matching(left) == normalize_for_matching(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # 別の数値・別の単位は同一視してはならない（正規化が行き過ぎていないこと）
        ("5mg", "50mg"),
        ("5mg", "5ml"),
        ("covid-19", "covid-18"),
    ],
)
def test_genuinely_different_values_stay_different(left, right):
    assert normalize_for_matching(left) != normalize_for_matching(right)


def test_japanese_text_is_not_mangled():
    """日本語の本文が正規化で壊れないこと（casefold/NFKC の巻き添えを防ぐ）。"""
    assert normalize_for_matching("胸痛のある患者") == "胸痛のある患者"


def test_digit_space_before_japanese_is_kept():
    """空白を詰めるのは欧字の単位の前だけ。日本語の前では詰めない。

    `5 日` を `5日` にしてしまうと、数量と助数詞の区切りが失われる。
    """
    assert normalize_for_matching("5 日") == "5 日"


def test_expand_iteration_marks_repeats_the_previous_character():
    assert expand_iteration_marks("日々") == "日日"
    # 先頭に来た場合は繰り返す対象が無いのでそのまま残す（例外にしない）。
    assert expand_iteration_marks("々") == "々"
