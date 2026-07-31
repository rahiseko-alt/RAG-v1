const form = document.querySelector("#ask-form");
const questionInput = document.querySelector("#question");
const askButton = document.querySelector("#ask-button");
const answer = document.querySelector("#answer");
const answerState = document.querySelector("#answer-state");
const sources = document.querySelector("#sources");
const providerStatus = document.querySelector("#provider-status");
const auditStatus = document.querySelector("#audit-status");
const modelStatus = document.querySelector("#model-status");
const knowledgeTitle = document.querySelector("#knowledge-title");
const knowledgeSource = document.querySelector("#knowledge-source");
const knowledgeConfig = document.querySelector("#knowledge-config");
const processDetails = document.querySelector("#process-details");
const timelineItems = Array.from(document.querySelectorAll("#timeline li"));

const steps = ["received", "knowledge", "searching", "sources", "generating", "audit", "complete"];

const pendingProcessSteps = [
  {
    id: "received",
    title: "質問受付",
    status: "待機中",
    purpose: "質問を検索に渡せる形にする。",
    input: "利用者が入力した質問。",
    process: "空白を除去し、空でないことを確認する。",
    output: "正規化済みの質問。",
    check: "質問が曖昧すぎる場合は、検索候補も曖昧になる。",
  },
  {
    id: "knowledge",
    title: "ナレッジ選択",
    status: "待機中",
    purpose: "どの文書に対して質問するかを確定する。",
    input: "config/knowledge.toml の設定。",
    process: "本文パス、出典URL、collection、評価セットを読む。",
    output: "今回使うナレッジIDと文書名。",
    check: "ここが想定外なら、回答以前に設定ミス。",
  },
  {
    id: "searching",
    title: "意味検索",
    status: "待機中",
    purpose: "質問に近い文書チャンクを探す。",
    input: "質問文と登録済みチャンク。",
    process: "質問をベクトル化し、Chromaで近い候補を取る。",
    output: "上位チャンクと検索スコア。",
    check: "スコアは正確性ではなく近さ。根拠の中身を見る。",
  },
  {
    id: "sources",
    title: "根拠候補確認",
    status: "待機中",
    purpose: "回答に必要な情報が候補にあるか見る。",
    input: "検索で取れた上位チャンク。",
    process: "出典、chunk_id、抜粋、スコアを表示する。",
    output: "回答に渡す根拠候補。",
    check: "必要情報が無いなら、回答は断定してはいけない。",
  },
  {
    id: "generating",
    title: "回答生成",
    status: "待機中",
    purpose: "根拠候補だけで回答を作る。",
    input: "質問と根拠候補。",
    process: "抜粋だけを根拠にする制約付きプロンプトでLLMへ渡す。",
    output: "出典番号付きの日本語回答。",
    check: "回答内容が根拠候補と一致しているか確認する。",
  },
  {
    id: "audit",
    title: "監査ログ",
    status: "待機中",
    purpose: "後から処理を追えるようにする。",
    input: "質問、検索、生成、モデル、ナレッジ情報。",
    process: "LangfuseがONならトレース送信する。",
    output: "監査ログまたはローカル応答のみ。",
    check: "問題があればログを書き換えず、設定を直して再実行する。",
  },
];

function setTimeline(activeStep, doneSteps = []) {
  timelineItems.forEach((item) => {
    const step = item.dataset.step;
    item.classList.toggle("active", step === activeStep);
    item.classList.toggle("done", doneSteps.includes(step));
  });
}

function setAnswerState(text, status = "") {
  answerState.textContent = text;
  answerState.className = `state-pill ${status}`.trim();
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderSources(items) {
  if (!items.length) {
    sources.innerHTML = '<div class="source-placeholder">根拠候補は見つかりませんでした。</div>';
    return;
  }

  sources.innerHTML = items
    .map((item) => {
      const score = Number(item.score).toFixed(3);
      return `
        <article class="source-card">
          <header>
            <span>[${item.rank}] ${escapeHtml(item.source)} p.${escapeHtml(item.page)}</span>
            <span class="meta">検索スコア ${score}</span>
          </header>
          <p>${escapeHtml(item.snippet)}</p>
        </article>
      `;
    })
    .join("");
}

function renderProcessSteps(items, activeId = null) {
  if (!items || !items.length) {
    processDetails.innerHTML =
      '<div class="process-placeholder">質問すると、各工程の目的・入力・処理・出力・判断目安がここに表示されます。</div>';
    return;
  }

  processDetails.innerHTML = items
    .map((item, index) => {
      const isActive = item.id === activeId;
      return `
        <article class="process-card ${isActive ? "active" : ""}">
          <header>
            <span class="step-index">${index + 1}</span>
            <div>
              <h3>${escapeHtml(item.title)}</h3>
              <span class="process-status">${escapeHtml(item.status)}</span>
            </div>
          </header>
          <dl>
            <div><dt>目的</dt><dd>${escapeHtml(item.purpose)}</dd></div>
            <div><dt>入力</dt><dd>${escapeHtml(item.input)}</dd></div>
            <div><dt>処理</dt><dd>${escapeHtml(item.process)}</dd></div>
            <div><dt>出力</dt><dd>${escapeHtml(item.output)}</dd></div>
            <div><dt>判断目安</dt><dd>${escapeHtml(item.check)}</dd></div>
          </dl>
        </article>
      `;
    })
    .join("");
}

async function loadHealth() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    providerStatus.textContent = `provider: ${data.provider}`;
    modelStatus.textContent = `model: ${data.model}`;
    auditStatus.textContent = data.langfuse_configured ? "監査: ON" : "監査: OFF";
    if (data.knowledge) {
      knowledgeTitle.textContent = data.knowledge.title || data.knowledge.id || "未設定";
      knowledgeConfig.textContent = data.knowledge.config_path || "-";
      knowledgeSource.textContent = data.knowledge.source || data.knowledge.source_url || "未設定";
      if (data.knowledge.source_url) {
        knowledgeSource.href = data.knowledge.source_url;
      } else {
        knowledgeSource.removeAttribute("href");
      }
    }
  } catch (_error) {
    providerStatus.textContent = "provider: 未確認";
    modelStatus.textContent = "model: 未確認";
    auditStatus.textContent = "監査: 未確認";
    knowledgeTitle.textContent = "未確認";
    knowledgeSource.textContent = "未確認";
    knowledgeSource.removeAttribute("href");
    knowledgeConfig.textContent = "-";
  }
}

async function askQuestion(question) {
  setTimeline("received");
  renderProcessSteps(pendingProcessSteps.map((step) => ({ ...step, status: step.id === "received" ? "処理中" : "待機中" })), "received");
  setAnswerState("処理中");
  answer.classList.add("empty");
  answer.textContent = "質問を受け取りました。文書検索を開始します。";
  sources.innerHTML = '<div class="source-placeholder">登録済みナレッジを検索しています。</div>';

  await new Promise((resolve) => setTimeout(resolve, 250));
  setTimeline("knowledge", ["received"]);
  renderProcessSteps(pendingProcessSteps.map((step) => ({
    ...step,
    status: step.id === "received" ? "完了" : step.id === "knowledge" ? "処理中" : "待機中",
  })), "knowledge");

  await new Promise((resolve) => setTimeout(resolve, 250));
  setTimeline("searching", ["received", "knowledge"]);
  renderProcessSteps(pendingProcessSteps.map((step) => ({
    ...step,
    status: ["received", "knowledge"].includes(step.id) ? "完了" : step.id === "searching" ? "処理中" : "待機中",
  })), "searching");

  await new Promise((resolve) => setTimeout(resolve, 250));
  setTimeline("generating", ["received", "knowledge", "searching", "sources"]);
  renderProcessSteps(pendingProcessSteps.map((step) => ({
    ...step,
    status: ["received", "knowledge", "searching", "sources"].includes(step.id) ? "完了" : step.id === "generating" ? "処理中" : "待機中",
  })), "generating");
  answer.textContent = "関連箇所を抽出し、根拠だけを渡して回答を生成しています。";

  const response = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      session_id: `ui-${Date.now()}`,
      trace_tags: ["ui-demo", "transparent-rag"],
    }),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "質問処理に失敗しました。");
  }

  setTimeline("audit", ["received", "knowledge", "searching", "sources", "generating"]);
  await new Promise((resolve) => setTimeout(resolve, 180));
  setTimeline(null, steps);
  renderProcessSteps(data.process_steps || pendingProcessSteps);
  answer.classList.remove("empty");
  answer.textContent = data.answer;
  renderSources(data.sources || []);
  setAnswerState("完了", "ok");
  auditStatus.textContent = data.audit_enabled ? "監査: ON" : "監査: OFF";
  modelStatus.textContent = `model: ${data.model}`;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  askButton.disabled = true;
  try {
    await askQuestion(question);
  } catch (error) {
    setTimeline(null, []);
    setAnswerState("エラー", "error");
    answer.classList.remove("empty");
    answer.textContent = error.message;
    sources.innerHTML = '<div class="source-placeholder">エラーのため根拠候補を表示できません。</div>';
  } finally {
    askButton.disabled = false;
  }
});

loadHealth();
