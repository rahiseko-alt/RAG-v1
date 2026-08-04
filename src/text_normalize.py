"""語の一致判定に使う表記の正規化。

検索（`src/rag`）と構造化ナレッジ検索（`src/structured_knowledge`）の双方が使う。
`src/rag` は langchain / torch を引き込むため、軽量なこのモジュールに置いて
双方から import する（構造化側に重い依存を持ち込まないため）。

ここで扱うのは**言語・表記のレベル**の揺れだけで、コーパス固有の語彙は扱わない
（それは `config/knowledge.toml` の `[lexical]` 節の担当）。
"""
from __future__ import annotations

import re
import unicodedata

# 数字と、それに続く欧字の間の空白を詰める（"5 mg" と "5mg" を同じ形にする）。
# casefold 済みの文字列に当てるため小文字だけを見れば足りる。
_DIGIT_UNIT_SPACE = re.compile(r"(\d)\s+(?=[a-z])")


def expand_iteration_marks(value: str) -> str:
    """繰り返し記号を展開する（`日々` → `日日`）。

    検索語の切り出しは記号を語の区切りとして扱うため、展開しておかないと
    `日々` が語として取れない。
    """
    chars: list[str] = []
    for char in value:
        if char == "々" and chars:
            chars.append(chars[-1])
        else:
            chars.append(char)
    return "".join(chars)


def normalize_for_matching(value: str) -> str:
    """一致判定に使う正規形へ揃える。

    **質問側と本文側の両方に同じ関数を通すことが前提。** 片側だけに適用すると、
    正規化そのものが不一致の原因になる。

    吸収する表記ゆれ:

    - 繰り返し記号 — `日々` → `日日`
    - 全角と半角 — `ＣＯＶＩＤ-19` → `covid-19`、`５ｍｇ` → `5mg`（NFKC）
    - 大文字と小文字 — `COVID-19` と `covid-19`（casefold）
    - 数値と単位の間の空白 — `5 mg` → `5mg`

    NFKC を先に掛けるのは、全角英数を半角に落としてから casefold と
    空白詰めを効かせるため。全角スペースもここで半角スペースになる。
    """
    text = unicodedata.normalize("NFKC", value)
    text = expand_iteration_marks(text)
    text = text.casefold()
    return _DIGIT_UNIT_SPACE.sub(r"\1", text)
