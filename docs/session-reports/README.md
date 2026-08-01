# session-reports について

各セッションのチェックアウト記録。**過去の事実の記録なので、後から書き換えない。**
「今の正しい状態」は `memory.md`（不変層）と `docs/handoff.md`（揮発層）を見ること。

`memory.md` が「次セッション必読」として指しているレポートは、チェックイン時に必ず読む。

## 旧パス・旧ブランチ名の読み替え

このプロジェクトは 3 つの場所を経由している。古いレポートの中の記述は**当時は正しかった**もので、
訂正せずそのまま残してある。読むときは次のように読み替えること。

| レポート中の記述 | 現在 |
|---|---|
| `products/medguide-rag/<path>` | `<path>`（リポジトリ直下） |
| `branch: master` | 当時の vibe-base monorepo のブランチ。以降 `codex/public-delivery-workbench` を経て現在は `main` |
| `docs/deliverables/development-career-summary-technical.md` | 分割元の vibe-base 側にあり、本リポジトリには無い |
| `docs/ops/agent-rules.md` / `docs/ops/product-cycle.md` | 同上（vibe-base 側）。現在の統治は `AGENTS.md` |
| `notebooks/outputs/*.png` | `.gitignore` 対象で未追跡。図は `notebooks/*.html` に埋め込み済み |

## 経緯

1. **vibe-base monorepo** の `products/medguide-rag/` として開発
2. **`codex/public-delivery-workbench`** ブランチとして単独リポジトリへ分割（`0fa8d37`）
3. **本リポジトリの `main`** へ取り込み（2026-08-01）。13 コミットの履歴は
   `--allow-unrelated-histories` のマージで祖先として保存してある
   （`c6335e8` 等、レポートが evidence として挙げる commit SHA は現在も到達可能）
