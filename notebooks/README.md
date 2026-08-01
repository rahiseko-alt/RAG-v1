# notebooks

Stage 1・Stage 2 の土台作業用ノートブックを置くディレクトリ。

- `01-foundations.ipynb`（Stage 1・完了）: pandas/numpyでのデータ前処理・簡易統計・可視化＋医学的定説照合
- `02-baseline-model.ipynb`（Stage 2・完了）: scikit-learnのロジスティック回帰で心疾患二値分類を1本完走（学習→評価→GridSearchCVチューニング）＋係数と定説の照合
- `03-rag-walkthrough.ipynb`（Stage 3・完了）: WHO HEARTS 文書のRAG（ingest→埋め込み→Chroma→LangGraph検索→根拠付き日本語回答）を体験。核＝出典追跡パネル。生成セルは `.env` の ANTHROPIC_API_KEY 設定時に実行
- `outputs/`: 各ノートが生成する図（Stage1: 00〜07／Stage2: 08〜11）。`.ipynb` は実行時に `outputs/` へ保存する前提で、`notebooks/` を作業ディレクトリとして実行する

このディレクトリの内容は前面に出すポートフォリオの主役ではなく、Stage 3・4（RAG・評価ループ）の土台として位置づける。
