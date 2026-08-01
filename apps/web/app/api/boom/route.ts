// 観測性の自己テスト用エンドポイント（監視/ログが失敗を捕捉することの検証用）。
// GET すると必ず 500 を返し、Vercel の Runtime Logs にエラーイベントとして現れる。
// 平常運用では叩かない。監視が「失敗を捕捉できる」ことを確認するための意図的な失敗源。
export function GET() {
  throw new Error("intentional failure for observability self-test");
}
