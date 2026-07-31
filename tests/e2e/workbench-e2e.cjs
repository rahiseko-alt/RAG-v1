const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("../../../../node_modules/playwright");

const BASE_URL = process.env.MEDGUIDE_E2E_URL || "http://127.0.0.1:8010/";
const QA_DIR = path.resolve(__dirname, "../../reports/qa");

const blockedResponse = {
  question: "E2E blocked question",
  run_id: "e2e-run",
  delivery_status: "blocked",
  answer: null,
  candidate_answer: "SECRET_BLOCKED_CANDIDATE",
  blocked_reason: "quality_gate_failed",
  verification: {
    status: "block",
    axes: { faithfulness: 1, relevance: 2, no_misinfo: 1 },
    claims: [
      {
        claim_id: "blocked-claim-1",
        status: "unclear",
        evidence: [{ rank: 1, reason: "Evidence classification." }],
        reason: "Claim classified as unclear.",
      },
    ],
  },
  audit: { status: "flushed", trace_id: "a".repeat(32), trace_url: null },
  sources: [
    {
      rank: 1,
      source: "fixture.md",
      page: 0,
      chunk_id: 12,
      score: 0.861,
      snippet: "E2E evidence fixture.",
    },
  ],
  chunks_indexed: 12,
  model: "e2e-model",
  audit_enabled: true,
  knowledge: { title: "E2E knowledge" },
  process_steps: [
    "質問受付",
    "ナレッジ選択",
    "意味検索",
    "根拠候補確認",
    "回答生成",
    "回答照合",
    "出荷判定",
    "監査記録",
  ].map((title, index) => ({
    id: `step-${index + 1}`,
    title,
    status:
      title === "回答照合"
        ? "NG"
        : title === "出荷判定"
          ? "出荷停止"
          : "完了",
    purpose: `${title}の目的`,
    input: `${title}の入力`,
    process: `${title}で実行した処理`,
    output: `${title}の出力`,
    check: `${title}の判断基準`,
  })),
};

async function assertBaseLayout(page) {
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  assert.equal(await page.locator('[role="tab"]').count(), 4);
  const question = await page
    .locator('[aria-labelledby="question-title"]')
    .boundingBox();
  const answer = await page
    .locator('[aria-labelledby="answer-title"]')
    .boundingBox();
  assert.ok(question && answer && question.y + question.height < answer.y);
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
    0,
  );
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  try {
    const desktop = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await desktop.route("**/ask", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(blockedResponse),
      });
    });
    await assertBaseLayout(desktop);
    await desktop.getByLabel("質問", { exact: true }).fill("E2E blocked question");
    await desktop.getByRole("button", { name: "回答を作成" }).click();
    await desktop.locator("#answer-state").filter({ hasText: "出荷停止" }).waitFor();
    assert.equal(await desktop.locator("#process-steps details").count(), 8);
    assert.equal(
      await desktop.locator("#process-steps details[open]").count(),
      2,
    );
    assert.equal(
      (await desktop.locator("body").innerText()).includes(
        "SECRET_BLOCKED_CANDIDATE",
      ),
      false,
    );

    for (const tabName of ["ナレッジ調整", "比較・承認", "台帳・監査"]) {
      await desktop.getByRole("tab", { name: tabName }).click();
      assert.equal(
        await desktop.getByRole("tabpanel", { name: tabName }).isVisible(),
        true,
      );
    }
    await desktop.getByRole("tab", { name: "質問・検品" }).click();
    await desktop.screenshot({
      path: path.join(QA_DIR, "workbench-desktop.jpg"),
      type: "jpeg",
      quality: 85,
      fullPage: true,
    });

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await assertBaseLayout(mobile);
    assert.equal(await mobile.evaluate(() => window.innerWidth), 390);
    await mobile.screenshot({
      path: path.join(QA_DIR, "workbench-mobile.jpg"),
      type: "jpeg",
      quality: 85,
      fullPage: true,
    });

    process.stdout.write(
      JSON.stringify({
        status: "PASS",
        desktop: "1280x900",
        mobile: "390x844",
        assertions: 10,
      }),
    );
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
