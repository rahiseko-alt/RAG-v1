// ヘルスチェック用エンドポイント（監視方針=Vercelログ＋ヘルスチェックを裏打ち）。
// 200 + {"status":"ok"} を返すだけの軽量ルート。外形監視やスモークから叩く。
export function GET() {
  return Response.json({ status: "ok" }, { status: 200 });
}
