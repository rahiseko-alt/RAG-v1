# data/sample

RAGの検索対象とする、公開されている文書・学習用データのサンプルを数件置くディレクトリ。

- ライセンス上再配布可能な文書のみを置く（出典を必ず明記する）
- 大きな生データ（データセット丸ごと等）は `data/raw/`（gitignore対象）に置き、ここには数件のサンプルのみ置く

---

## データカード: jujutsu-kaisen-wikipedia.md

現在のRAGデモで既定の検索対象にしている要約ナレッジ。実際にどのナレッジを使うかは `config/knowledge.toml` で指定する。

| 項目 | 内容 |
|---|---|
| 名称 | 呪術廻戦 拡張リサーチナレッジ |
| 原典 | 日本語版Wikipedia「呪術廻戦」、日本語版Wikipedia「呪術廻戦 (アニメ)」、少年ジャンプ公式、TVアニメ公式、集英社公式ファンブック書誌ページ |
| 観測ソース | Reddit、5ちゃんねる、Yahoo!知恵袋、YouTube解説/考察動画などの公開情報から、本文を保存せず論点カテゴリのみ抽出 |
| 原典URL | https://ja.wikipedia.org/wiki/%E5%91%AA%E8%A1%93%E5%BB%BB%E6%88%A6 / https://ja.wikipedia.org/wiki/%E5%91%AA%E8%A1%93%E5%BB%BB%E6%88%A6_(%E3%82%A2%E3%83%8B%E3%83%A1) / https://www.shonenjump.com/j/rensai/jujutsu/ / https://jujutsukaisen.jp/ / https://books.shueisha.co.jp/items/contents.html?isbn=978-4-08-882636-3 |
| 確認日 | 2026-07-31 |
| ライセンス | CC BY-SA |
| 用途 | 透明型RAG UIのデモ用ナレッジ。原文そのものではなく、RAG検証用に要約・再構成したMarkdown |
| 注意 | 台詞、全話サブタイトル、公式ファンブック詳細Q&A、SNS/掲示板投稿本文、YouTube字幕・コメント本文の転載は含めない。詳細・最新情報・Wikipedia脚注は原ページを確認する |

---

## データカード: heart-disease-cleveland.csv

Stage 1（Python基礎固め）で使う練習用データセット。

| 項目 | 内容 |
|---|---|
| 名称 | Heart Disease Data Set（Cleveland, processed） |
| 出典 | UCI Machine Learning Repository (ID 45) — https://archive.ics.uci.edu/dataset/45/heart+disease |
| 取得元URL | https://archive.ics.uci.edu/ml/machine-learning-databases/heart-disease/processed.cleveland.data |
| ライセンス | CC BY 4.0（帰属表示のもと再配布可） |
| 引用 | Janosi, Steinbrunn, Pfisterer, Detrano (1988). Heart Disease. UCI Machine Learning Repository. |
| 件数 | 303行 × 14列 |
| 欠損 | `ca`・`thal` 列に欠損記号 `?` を含む（前処理の練習対象） |
| 取得方法 | urllib で上記URLから取得し、14列の列名を付与してUTF-8 CSVで保存 |

### 列辞書（14列）

| 列 | 意味 | 型/値 |
|---|---|---|
| age | 年齢 | 数値 |
| sex | 性別 | 1=男性, 0=女性 |
| cp | 胸痛タイプ | 1〜4 |
| trestbps | 安静時血圧 (mmHg) | 数値 |
| chol | 血清コレステロール (mg/dl) | 数値 |
| fbs | 空腹時血糖>120mg/dl | 1=真, 0=偽 |
| restecg | 安静時心電図結果 | 0〜2 |
| thalach | 最大心拍数 | 数値 |
| exang | 運動誘発狭心症 | 1=有, 0=無 |
| oldpeak | 運動によるST低下 | 数値 |
| slope | ピーク運動STの傾き | 1〜3 |
| ca | 蛍光透視で着色した主要血管数 | 0〜3（`?` 欠損あり） |
| thal | サラセミア | 3=正常, 6=固定欠損, 7=可逆欠損（`?` 欠損あり） |
| num | **目的変数**: 心疾患の診断 | 0=なし, 1〜4=あり（Stage1では 0/1 に二値化） |

---

## データカード: who-hearts-healthy-lifestyle-counselling.pdf

旧Stage 3（医療文書RAG）で検索対象にしていた医療ガイドライン文書。現在の既定検索対象は `jujutsu-kaisen-wikipedia.md`。

| 項目 | 内容 |
|---|---|
| 名称 | HEARTS Technical Package — Healthy-lifestyle counselling |
| 発行機関 | World Health Organization (WHO), 2018 |
| ドメイン/言語 | 循環器疾患予防（生活習慣カウンセリング）/ 英語・全30ページ |
| 書誌 | https://www.who.int/publications/i/item/WHO-NMH-NVI-18-1 |
| 取得元URL | https://iris.who.int/server/api/core/bitstreams/0c21ba41-60db-4592-a97c-60a8b62033eb/content |
| **ライセンス** | **CC BY-NC-SA 3.0 IGO**（非営利・帰属表示・継承の条件で再配布/翻訳/改変が可能） |
| ライセンス根拠 | WHO 出版著作権ポリシー https://www.who.int/about/policies/publishing/copyright ／ 条文 https://creativecommons.org/licenses/by-nc-sa/3.0/igo/legalcode.en |
| 帰属表示 | © World Health Organization 2018. HEARTS technical package: Healthy-lifestyle counselling. Licensed under CC BY-NC-SA 3.0 IGO. |
| 取得方法 | urllib で上記URLから取得し `data/sample/` に無改変で保存（同梱＝再配布） |
| 用途 | RAG のチャンク分割・埋め込み・検索の題材。原文は無改変で同梱し、チャンク処理はコード実行時に動的に行う |

> 本プロジェクトは**非営利の個人学習ポートフォリオ**であり、CC BY-NC-SA 3.0 IGO の非営利条件に適合する。二次的著作物（本リポジトリ）も同一ライセンス（継承）の趣旨に沿って扱う。回答生成は文書の**要約・引用**であり医療助言ではない。
