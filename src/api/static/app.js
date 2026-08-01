const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const elements = {
  tabs: $$('[role="tab"]'),
  panels: $$('[role="tabpanel"]'),
  globalStatus: $("#global-status"),
  serviceStatus: $("#service-status"),
  healthStatus: $("#health-status"),
  providerStatus: $("#provider-status"),
  modelStatus: $("#model-status"),
  auditStatus: $("#audit-status"),
  knowledgeTitle: $("#knowledge-title"),
  knowledgeConfig: $("#knowledge-config"),
  knowledgeSource: $("#knowledge-source"),
  refreshHealth: $("#refresh-health"),
  askForm: $("#ask-form"),
  question: $("#question"),
  answerMode: $("#answer-mode"),
  askButton: $("#ask-button"),
  answerRegion: $("#answer-region"),
  answer: $("#answer"),
  answerState: $("#answer-state"),
  blockedAction: $("#blocked-action"),
  openKnowledgeTab: $("#open-knowledge-tab"),
  verification: $("#verification"),
  claims: $("#claims"),
  processSteps: $("#process-steps"),
  revisionForm: $("#revision-form"),
  revisionOperator: $("#revision-operator"),
  revisionSummary: $("#revision-summary"),
  revisionTitle: $("#revision-title"),
  revisionSourceUrl: $("#revision-source-url"),
  revisionFile: $("#revision-file"),
  revisionContent: $("#revision-content"),
  createRevision: $("#create-revision"),
  revisionFormStatus: $("#revision-form-status"),
  refreshRevisions: $("#refresh-revisions"),
  revisionList: $("#revision-list"),
  comparisonForm: $("#comparison-form"),
  comparisonRevision: $("#comparison-revision"),
  comparisonQuestion: $("#comparison-question"),
  startComparison: $("#start-comparison"),
  startValidation: $("#start-validation"),
  comparisonFormStatus: $("#comparison-form-status"),
  jobList: $("#job-list"),
  comparisonResult: $("#comparison-result"),
  approveRevision: $("#approve-revision"),
  approvalNote: $("#approval-note"),
  refreshLedger: $("#refresh-ledger"),
  ledgerSummary: $("#ledger-summary"),
  traceDetail: $("#trace-detail"),
};

const state = {
  revisions: [],
  jobs: new Map(),
  pollTimers: new Map(),
  approval: { allowed: false, revisionId: "" },
  loadedTabs: new Set(["question"]),
  runEvents: [],
  runEventSource: null,
};

const pendingProcessSteps = [
  {
    id: "received",
    title: "質問受付",
    status: "active",
    purpose: "入力された質問を受け付けます。",
    input: "質問文",
    process: "空白を除去し、処理可能な入力か確認します。",
    output: "正規化した質問",
    check: "質問が空欄でないこと",
  },
  {
    id: "knowledge",
    title: "ナレッジ選択",
    status: "pending",
    purpose: "回答に使う登録済み文書を確定します。",
    input: "有効なナレッジ設定",
    process: "文書・出典・検索設定を読み込みます。",
    output: "対象ナレッジ",
    check: "意図した文書が選ばれていること",
  },
  {
    id: "searching",
    title: "意味検索",
    status: "pending",
    purpose: "質問に近い文書箇所を探します。",
    input: "質問と登録済みチャンク",
    process: "質問との検索上の近さで候補を並べます。",
    output: "検索候補",
    check: "類似度は正しさではなく検索上の近さです",
  },
  {
    id: "evidence-review",
    title: "根拠候補確認",
    status: "pending",
    purpose: "検索で取得した候補の中身と出典を確認します。",
    input: "順位付き検索候補",
    process: "チャンクID・出典・原文抜粋を回答生成へ渡せる形に整理します。",
    output: "番号付き根拠候補",
    check: "取得件数と各候補の原文を展開確認できること",
  },
  {
    id: "generating",
    title: "回答生成",
    status: "pending",
    purpose: "検索候補だけを使って回答候補を作ります。",
    input: "質問と検索候補",
    process: "根拠外を補わない制約で回答を生成します。",
    output: "回答候補",
    check: "根拠にない断定がないこと",
  },
  {
    id: "verification",
    title: "回答照合",
    status: "pending",
    purpose: "回答を複数の観点で検品します。",
    input: "回答候補と根拠",
    process: "忠実性・関連性・誤情報・主張単位の支持を確認します。",
    output: "検証結果と出荷判定",
    check: "ブロックまたは不明な主張が残っていないこと",
  },
  {
    id: "release",
    title: "出荷判定",
    status: "pending",
    purpose: "検品済み回答を表示してよいか最終決定します。",
    input: "回答照合結果",
    process: "NG・判断不能・検品エラーはfail-closedで遮断します。",
    output: "回答可能または出荷停止",
    check: "出荷停止時に候補本文が公開されていないこと",
  },
  {
    id: "audit",
    title: "監査記録",
    status: "pending",
    purpose: "後から処理を追跡できるよう記録します。",
    input: "質問・検索・回答・検証結果",
    process: "run IDとトレース情報を保存します。",
    output: "監査情報",
    check: "runとtraceを参照できること",
  },
];

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (_error) {
    return "";
  }
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") return Object.values(value);
  return [];
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function formatValue(value, fallback = "—") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "boolean") return value ? "はい" : "いいえ";
  if (Array.isArray(value)) return value.length ? value.join(", ") : fallback;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("ja-JP", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function setGlobalStatus(message) {
  elements.globalStatus.textContent = message;
}

function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent.trim();
    button.disabled = true;
    button.textContent = busyLabel;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.textContent = button.dataset.label;
    delete button.dataset.label;
  }
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_error) {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const error = new Error(firstValue(data.detail, data.message, `HTTP ${response.status}`));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function switchTab(tabName, focus = true) {
  const target = elements.tabs.find((tab) => tab.id === `tab-${tabName}`);
  if (!target) return;

  elements.tabs.forEach((tab) => {
    const selected = tab === target;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  elements.panels.forEach((panel) => {
    panel.hidden = panel.id !== target.getAttribute("aria-controls");
  });
  if (focus) target.focus();

  if (!state.loadedTabs.has(tabName)) {
    state.loadedTabs.add(tabName);
    if (tabName === "knowledge" || tabName === "comparison") loadRevisions();
    if (tabName === "ledger") loadLedger();
  }
}

elements.tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => switchTab(tab.id.replace("tab-", ""), false));
  tab.addEventListener("keydown", (event) => {
    let nextIndex = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (index + 1) % elements.tabs.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (index - 1 + elements.tabs.length) % elements.tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = elements.tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    switchTab(elements.tabs[nextIndex].id.replace("tab-", ""));
  });
});

elements.openKnowledgeTab.addEventListener("click", () => switchTab("knowledge"));

function setKnowledgeSource(label, url) {
  elements.knowledgeSource.textContent = label || "未設定";
  const href = safeUrl(url);
  if (href) {
    elements.knowledgeSource.href = href;
    elements.knowledgeSource.target = "_blank";
    elements.knowledgeSource.rel = "noreferrer";
  } else {
    elements.knowledgeSource.removeAttribute("href");
    elements.knowledgeSource.removeAttribute("target");
    elements.knowledgeSource.removeAttribute("rel");
  }
}

async function loadHealth() {
  elements.serviceStatus.setAttribute("aria-busy", "true");
  elements.healthStatus.textContent = "確認中";
  try {
    const data = await apiFetch("/health");
    const knowledge = data.knowledge || {};
    elements.healthStatus.textContent = data.status === "ok" ? "稼働中" : formatValue(data.status, "応答あり");
    elements.providerStatus.textContent = formatValue(data.provider);
    elements.modelStatus.textContent = formatValue(data.model);
    elements.auditStatus.textContent = data.langfuse_configured || data.audit_enabled ? "有効" : "無効";
    elements.knowledgeTitle.textContent = firstValue(knowledge.title, knowledge.id, "未設定");
    elements.knowledgeConfig.textContent = firstValue(knowledge.config_path, data.top_k ? `top_k=${data.top_k}` : null, "—");
    setKnowledgeSource(firstValue(knowledge.source, knowledge.source_url, "未設定"), knowledge.source_url);

    elements.revisionTitle.value ||= firstValue(knowledge.title, "");
    elements.revisionSourceUrl.value ||= firstValue(knowledge.source_url, "");
  } catch (error) {
    elements.healthStatus.textContent = "接続失敗";
    elements.providerStatus.textContent = "未確認";
    elements.modelStatus.textContent = "未確認";
    elements.auditStatus.textContent = "未確認";
    elements.knowledgeTitle.textContent = "未確認";
    elements.knowledgeConfig.textContent = "—";
    setKnowledgeSource("未確認", "");
    setGlobalStatus(`サービス状態を取得できませんでした: ${error.message}`);
  } finally {
    elements.serviceStatus.setAttribute("aria-busy", "false");
  }
}

elements.refreshHealth.addEventListener("click", loadHealth);

function normalizedDeliveryStatus(data) {
  const raw = String(firstValue(data.delivery_status, data.status, "")).toLowerCase();
  if (["blocked", "stop", "stopped", "shipment_blocked", "出荷停止"].includes(raw)) return "blocked";
  if (["failed", "error", "processing_failed", "処理失敗"].includes(raw)) return "failed";
  if (["answerable", "ready", "completed", "complete", "ok", "回答可能"].includes(raw)) return "answerable";
  return data.answer ? "answerable" : "failed";
}

function setAnswerState(status) {
  const definitions = {
    idle: ["待機中", "state-idle"],
    working: ["処理中", "state-working"],
    answerable: ["回答可能", "state-ok"],
    blocked: ["出荷停止", "state-blocked"],
    failed: ["処理失敗", "state-error"],
  };
  const [label, className] = definitions[status] || definitions.failed;
  elements.answerState.textContent = label;
  elements.answerState.className = `state-badge ${className}`;
}

function scoreValue(value) {
  if (value && typeof value === "object") {
    return firstValue(value.score, value.value, value.median, value.label, formatValue(value));
  }
  return formatValue(value);
}

function claimCount(verification, key) {
  const aliases = {
    supported_claim_count: ["supported_claim_count", "supported_claims"],
    blocked_claims: ["blocked_claims", "blocked_claim_count"],
    unclear_claims: ["unclear_claims", "unclear_claim_count"],
  };
  const direct = firstValue(...(aliases[key] || [key]).map((alias) => verification[alias]));
  if (Array.isArray(direct)) return direct.length;
  if (direct !== undefined && direct !== null) return direct;
  const claims = asArray(verification.claims);
  if (key === "supported_claim_count") {
    return claims.filter((claim) => ["supported", "pass", "ok", "支持"].includes(String(claim.verdict || claim.status).toLowerCase())).length;
  }
  if (key === "blocked_claims") {
    return claims.filter((claim) => ["blocked", "ng", "unsupported", "contradicted", "矛盾", "ブロック"].includes(String(claim.verdict || claim.status).toLowerCase())).length;
  }
  if (key === "unclear_claims") {
    return claims.filter((claim) => ["unclear", "unknown", "needs_review", "不明"].includes(String(claim.verdict || claim.status).toLowerCase())).length;
  }
  return 0;
}

function renderVerification(verification = {}) {
  if (!verification || !Object.keys(verification).length) {
    elements.verification.innerHTML = '<p class="empty-message">この応答には検証情報がありません。</p>';
    return;
  }
  const axes = verification.axes || verification.scores || {};
  const metrics = [
    ["総合", firstValue(verification.status, verification.overall, verification.overall_score, verification.result)],
    ["根拠忠実性", firstValue(verification.faithfulness, axes.faithfulness)],
    ["質問関連性", firstValue(verification.relevance, axes.relevance)],
    ["誤情報なし", firstValue(verification.no_misinfo, axes.no_misinfo)],
    ["支持された主張", claimCount(verification, "supported_claim_count")],
    ["ブロック主張", claimCount(verification, "blocked_claims")],
    ["不明な主張", claimCount(verification, "unclear_claims")],
  ];
  elements.verification.innerHTML = metrics
    .map(
      ([label, value], index) => `
        <div class="metric ${index === 0 ? "metric-primary" : ""}">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(scoreValue(value))}</strong>
        </div>
      `,
    )
    .join("");
}

function verdictDefinition(value) {
  const raw = String(value || "unclear").toLowerCase();
  if (["supported", "pass", "passed", "ok", "支持", "確認済み"].includes(raw)) return ["支持", "verdict-ok"];
  if (["blocked", "fail", "failed", "ng", "unsupported", "ブロック", "不支持"].includes(raw)) return ["ブロック", "verdict-error"];
  return [value || "不明", "verdict-warn"];
}

function retrievalRelationLabel(value) {
  const raw = String(value || "direct");
  const labels = {
    direct: "ベクトル検索",
    neighbor: "隣接補完",
    bm25_rescue: "BM25補完",
    vector_bm25: "ベクトル+BM25",
    structured_entity: "構造化Entity",
    structured_fact: "構造化Fact",
    structured_record: "構造化Record",
    keyword_rescue: "キーワード補完",
    vector_keyword: "ベクトル+キーワード",
  };
  return labels[raw] || raw;
}

function retrievalScoreText(item) {
  const parts = [];
  const similarity = firstValue(item.similarity, item.score);
  if (similarity !== undefined && similarity !== null) parts.push(`類似度 ${formatValue(similarity)}`);
  if (item.bm25_score !== undefined && item.bm25_score !== null) parts.push(`BM25 ${formatValue(item.bm25_score)}`);
  if (item.rerank_score !== undefined && item.rerank_score !== null) parts.push(`統合 ${formatValue(item.rerank_score)}`);
  if (item.structured_score !== undefined && item.structured_score !== null) parts.push(`構造化 ${formatValue(item.structured_score)}`);
  return parts.join(" · ") || "スコア未設定";
}

function candidateMarkup(candidate, index) {
  const rank = firstValue(candidate.rank, candidate.source_rank, index + 1);
  const chunk = firstValue(candidate.chunk_id, candidate.chunk, "—");
  const snippet = firstValue(candidate.snippet, candidate.text, candidate.content, "抜粋なし");
  const source = firstValue(candidate.source, candidate.title, "出典未設定");
  const sourceUrl = safeUrl(candidate.source_url);
  const sourceId = firstValue(candidate.source_id, "ID未設定");
  const sourceType = firstValue(candidate.source_type, "種別未設定");
  const authority = firstValue(candidate.authority, "区分未設定");
  const fetchedAt = firstValue(candidate.fetched_at, "取得日時未設定");
  const relation = retrievalRelationLabel(candidate.retrieval_relation);
  const scores = retrievalScoreText(candidate);
  const recordId = candidate.structured_record_id ? ` · record ${escapeHtml(candidate.structured_record_id)}` : "";
  return `
    <li>
      <div><strong>候補 ${escapeHtml(rank)}</strong> · ${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">${escapeHtml(source)}</a>` : escapeHtml(source)} · ${escapeHtml(sourceId)} · ${escapeHtml(sourceType)} · ${escapeHtml(authority)} · ${escapeHtml(relation)} · chunk ${escapeHtml(chunk)}${recordId}</div>
      <p>${escapeHtml(snippet)}</p>
      <span>${escapeHtml(scores)} · 取得 ${escapeHtml(fetchedAt)}</span>
    </li>
  `;
}

function evidenceMarkup(evidence, index, claimCandidates = []) {
  const rank = firstValue(evidence.source_rank, evidence.rank, index + 1);
  const chunk = firstValue(evidence.chunk_id, evidence.chunk, "—");
  const snippet = firstValue(evidence.snippet, evidence.text, evidence.content, "抜粋なし");
  const source = firstValue(evidence.source, evidence.title, "出典未設定");
  const sourceUrl = safeUrl(evidence.source_url);
  const sourceId = firstValue(evidence.source_id, "ID未設定");
  const sourceType = firstValue(evidence.source_type, "種別未設定");
  const authority = firstValue(evidence.authority, "区分未設定");
  const fetchedAt = firstValue(evidence.fetched_at, "取得日時未設定");
  const relation = retrievalRelationLabel(evidence.retrieval_relation);
  const scores = retrievalScoreText(evidence);
  const recordId = evidence.structured_record_id ? `record ${evidence.structured_record_id}` : "";
  const candidates = asArray(firstValue(evidence.retrieved_candidates, evidence.candidates, claimCandidates));
  const candidateDetails = candidates.length
    ? `
      <details class="candidate-details">
        <summary>検索候補 ${escapeHtml(candidates.length)}件を表示</summary>
        <p class="similarity-note">類似度は検索上の近さであり、正しさの判定ではありません。</p>
        <ol>${candidates.map(candidateMarkup).join("")}</ol>
      </details>
    `
    : "";
  return `
    <div class="evidence">
      <div class="evidence-meta">
        <strong>出典順位 ${escapeHtml(rank)}</strong>
        <span>${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">${escapeHtml(source)}</a>` : escapeHtml(source)}</span>
        <span>${escapeHtml(sourceId)}</span>
        <span>${escapeHtml(sourceType)}</span>
        <span>${escapeHtml(authority)}</span>
        <span>${escapeHtml(relation)}</span>
        <span>chunk ${escapeHtml(chunk)}</span>
        ${recordId ? `<span>${escapeHtml(recordId)}</span>` : ""}
        <span>取得 ${escapeHtml(fetchedAt)}</span>
        <span>${escapeHtml(scores)}</span>
      </div>
      <blockquote>${escapeHtml(snippet)}</blockquote>
      ${candidateDetails}
    </div>
  `;
}

function normalizeClaims(data) {
  const verification = data.verification || {};
  const claims = asArray(firstValue(verification.claims, verification.claim_results, data.claims, data.audit?.claims));
  if (claims.length) return claims;
  return asArray(data.sources).length
    ? [
        {
          claim: firstValue(data.answer, "回答全体"),
          verdict: "unclear",
          evidence: data.sources,
          retrieved_candidates: data.sources,
        },
      ]
    : [];
}

function renderClaims(data) {
  const claims = normalizeClaims(data);
  if (!claims.length) {
    elements.claims.innerHTML = '<p class="empty-message">主張または根拠情報はありません。</p>';
    return;
  }
  elements.claims.innerHTML = claims
    .map((claim, index) => {
      const [verdict, verdictClass] = verdictDefinition(firstValue(claim.verdict, claim.status, claim.result));
      const text = firstValue(claim.claim, claim.text, claim.statement, `非公開の主張 ${index + 1}`);
      const evidence = asArray(firstValue(claim.supporting_sources, claim.supporting_evidence, claim.evidence, claim.sources));
      const candidates = asArray(
        firstValue(claim.retrieved_candidates, claim.candidates, claim.all_candidates, claim.retrieved, data.sources),
      );
      const mergedEvidence = evidence.map((item) => {
        const rank = Number(firstValue(item.source_rank, item.rank));
        const source = candidates.find((candidate) => Number(firstValue(candidate.source_rank, candidate.rank)) === rank);
        return source ? { ...source, ...item } : item;
      });
      return `
        <article class="claim">
          <header>
            <span class="claim-number">主張 ${escapeHtml(index + 1)}</span>
            <span class="verdict ${verdictClass}">${escapeHtml(verdict)}</span>
          </header>
          <p class="claim-text">${escapeHtml(text)}</p>
          <div class="evidence-list">
            ${
              mergedEvidence.length
                ? mergedEvidence.map((item, evidenceIndex) => evidenceMarkup(item, evidenceIndex, candidates)).join("")
                : '<p class="empty-message compact">支持する出典はありません。</p>'
            }
          </div>
        </article>
      `;
    })
    .join("");
}

function processStatus(value) {
  const raw = String(value || "pending").toLowerCase();
  if (["active", "running", "processing", "処理中", "実行中"].includes(raw)) return ["処理中", "process-active", true];
  if (["complete", "completed", "done", "pass", "passed", "完了", "ok"].includes(raw)) return ["完了", "process-complete", false];
  if (["failed", "error", "ng", "blocked", "失敗", "出荷停止"].includes(raw)) return [value || "失敗", "process-failed", true];
  return [value === "pending" ? "待機中" : value || "待機中", "process-pending", false];
}

function renderProcessSteps(steps) {
  const items = asArray(steps);
  if (!items.length) {
    elements.processSteps.innerHTML = '<p class="empty-message">処理工程の情報はありません。</p>';
    return;
  }
  elements.processSteps.innerHTML = items
    .map((step, index) => {
      const [statusLabel, statusClass, open] = processStatus(step.status);
      return `
        <details class="process-step ${statusClass}" ${open ? "open" : ""}>
          <summary>
            <span class="step-number">${escapeHtml(index + 1)}</span>
            <span class="step-title">${escapeHtml(firstValue(step.title, step.id, `工程 ${index + 1}`))}</span>
            <span class="process-status">${escapeHtml(statusLabel)}</span>
          </summary>
          <dl>
            <div><dt>目的</dt><dd>${escapeHtml(formatValue(step.purpose))}</dd></div>
            <div><dt>入力</dt><dd>${escapeHtml(formatValue(step.input))}</dd></div>
            <div><dt>処理</dt><dd>${escapeHtml(formatValue(step.process))}</dd></div>
            <div><dt>出力</dt><dd>${escapeHtml(formatValue(step.output))}</dd></div>
            <div><dt>確認</dt><dd>${escapeHtml(firstValue(step.check, step.checkpoint, "—"))}</dd></div>
          </dl>
        </details>
      `;
    })
    .join("");
}

function closeRunEventSource() {
  if (state.runEventSource) {
    state.runEventSource.close();
    state.runEventSource = null;
  }
}

function eventPayload(event) {
  return event?.payload || {};
}

function eventSummary(event) {
  const payload = eventPayload(event);
  const fields = [];
  if (payload.summary) fields.push(payload.summary);
  if (payload.revision_id) fields.push(`revision ${payload.revision_id}`);
  if (payload.source_sha256) fields.push(`source ${String(payload.source_sha256).slice(0, 12)}`);
  if (payload.structured_sha256) fields.push(`structured ${String(payload.structured_sha256).slice(0, 12)}`);
  if (payload.structured_hits !== undefined) fields.push(`構造化hit ${payload.structured_hits}`);
  if (payload.retrieved_sources !== undefined) fields.push(`取得根拠 ${payload.retrieved_sources}`);
  if (payload.claim_count !== undefined) fields.push(`主張 ${payload.claim_count}`);
  if (payload.delivery_status) fields.push(`出荷 ${payload.delivery_status}`);
  if (payload.blocked_reason) fields.push(`停止理由 ${payload.blocked_reason}`);
  if (payload.error) fields.push(payload.error);
  return fields.join(" / ") || JSON.stringify(payload);
}

function renderRunEventSteps(events) {
  const steps = pendingProcessSteps.map((step) => ({ ...step, status: "pending" }));
  const byId = Object.fromEntries(steps.map((step) => [step.id, step]));
  const mark = (id, status, event, overrides = {}) => {
    const step = byId[id];
    if (!step) return;
    const summary = event ? eventSummary(event) : "";
    Object.assign(step, {
      status,
      input: overrides.input || step.input,
      process: overrides.process || `実イベント ${event?.event_type || ""} を受信しました。`,
      output: overrides.output || summary || step.output,
      check: overrides.check || step.check,
    });
  };

  for (const event of events) {
    const payload = eventPayload(event);
    switch (event.event_type) {
      case "queued":
        mark("received", "active", event, { output: "runを作成し、処理キューへ登録しました。" });
        break;
      case "running":
      case "question_received":
        mark("received", "completed", event, {
          input: `質問長 ${payload.input?.question_length ?? "—"} / mode ${payload.answer_mode || "—"}`,
        });
        break;
      case "knowledge_selected":
        mark("knowledge", "completed", event);
        break;
      case "retrieve_started":
        mark("searching", "active", event);
        mark("evidence-review", "active", event);
        break;
      case "generate_completed":
        mark("searching", "completed", event);
        mark("evidence-review", "completed", event);
        mark("generating", "completed", event);
        break;
      case "verify_completed":
        mark("verification", payload.release_allowed ? "completed" : "blocked", event);
        break;
      case "gate_completed":
        mark("release", payload.delivery_status === "blocked" ? "blocked" : "completed", event);
        break;
      case "completed":
        mark("audit", "completed", event);
        break;
      case "failed":
        mark("audit", "failed", event);
        break;
      default:
        break;
    }
  }
  renderProcessSteps(steps);
}

async function waitForRun(runId) {
  state.runEvents = [];
  renderRunEventSteps(state.runEvents);
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      closeRunEventSource();
      reject(new Error("run event stream timed out"));
    }, 70000);
    const finish = async () => {
      window.clearTimeout(timeout);
      closeRunEventSource();
      const status = await apiFetch(`/runs/${encodeURIComponent(runId)}`);
      resolve(status);
    };

    if (!window.EventSource) {
      const poll = async () => {
        const status = await apiFetch(`/runs/${encodeURIComponent(runId)}`);
        state.runEvents = status.events || [];
        renderRunEventSteps(state.runEvents);
        if (["completed", "failed"].includes(status.status)) {
          window.clearTimeout(timeout);
          resolve(status);
          return;
        }
        window.setTimeout(poll, 750);
      };
      poll().catch(reject);
      return;
    }

    closeRunEventSource();
    state.runEventSource = new EventSource(`/runs/${encodeURIComponent(runId)}/events`);
    state.runEventSource.onmessage = () => {};
    const eventTypes = [
      "queued",
      "running",
      "question_received",
      "knowledge_selected",
      "retrieve_started",
      "generate_completed",
      "verify_completed",
      "gate_completed",
      "completed",
      "failed",
    ];
    for (const type of eventTypes) {
      state.runEventSource.addEventListener(type, (message) => {
        const event = JSON.parse(message.data);
        state.runEvents.push(event);
        renderRunEventSteps(state.runEvents);
        if (type === "completed" || type === "failed") {
          finish().catch(reject);
        }
      });
    }
    state.runEventSource.onerror = () => {
      closeRunEventSource();
      apiFetch(`/runs/${encodeURIComponent(runId)}`).then(resolve).catch(reject);
    };
  });
}

function showAskFailure(message) {
  setAnswerState("failed");
  elements.answer.textContent = message;
  elements.blockedAction.hidden = true;
  elements.verification.innerHTML = '<p class="empty-message">処理失敗のため検証結果はありません。</p>';
  elements.claims.innerHTML = '<p class="empty-message">処理失敗のため主張と根拠は表示できません。</p>';
  renderProcessSteps(
    pendingProcessSteps.map((step, index) => ({
      ...step,
      status: index === 0 ? "failed" : "pending",
      output: index === 0 ? message : step.output,
    })),
  );
}

async function askQuestion(question) {
  setAnswerState("working");
  elements.answerRegion.setAttribute("aria-busy", "true");
  elements.answer.textContent = "回答候補を作成し、根拠と検証結果を確認しています。";
  elements.blockedAction.hidden = true;
  elements.verification.innerHTML = '<p class="empty-message">検証中です。</p>';
  elements.claims.innerHTML = '<p class="empty-message">根拠を整理しています。</p>';
  renderProcessSteps(pendingProcessSteps);

  try {
    const created = await apiFetch("/runs", {
      method: "POST",
      body: JSON.stringify({
        question,
        answer_mode: elements.answerMode?.value || "standard",
        session_id: `workbench-${Date.now()}`,
        trace_tags: ["workbench", "non-engineer-review"],
      }),
    });
    setGlobalStatus(`処理を開始しました。run ID: ${created.run_id}`);
    const run = await waitForRun(created.run_id);
    if (run.status === "failed") {
      showAskFailure(firstValue(run.error_message, "回答処理に失敗しました。"));
      return;
    }
    const data = run.response;
    if (!data) {
      showAskFailure("runは完了しましたが、回答レスポンスが保存されていません。");
      return;
    }
    const deliveryStatus = normalizedDeliveryStatus(data);

    if (deliveryStatus === "blocked") {
      setAnswerState("blocked");
      const blockedMessages = {
        quality_gate_failed: "回答中に根拠で確認できない主張があるため、出荷を停止しました。",
        standard_gate_failed: "通常QAの条件を満たしませんでした。矛盾、根拠不足、または誤情報の可能性があります。",
        explore_gate_failed: "探索モードでも表示できない矛盾または根拠不備があるため、出荷を停止しました。",
        verifier_timeout: "回答照合が時間内に完了しなかったため、出荷を停止しました。",
        verifier_error: "回答照合に失敗したため、安全側に倒して出荷を停止しました。",
        verifier_initialization_error: "回答照合を開始できなかったため、出荷を停止しました。",
      };
      elements.answer.textContent =
        blockedMessages[data.blocked_reason] ||
        firstValue(data.blocked_reason, "検証条件を満たさないため、回答の出荷を停止しました。");
      elements.blockedAction.hidden = false;
    } else if (deliveryStatus === "failed") {
      showAskFailure(firstValue(data.blocked_reason, data.detail, data.message, "回答処理に失敗しました。"));
      return;
    } else {
      setAnswerState("answerable");
      elements.answer.textContent = firstValue(data.answer, "回答本文がありません。");
      elements.blockedAction.hidden = true;
    }

    renderVerification(data.verification || {});
    renderClaims(data);
    renderProcessSteps(data.process_steps || pendingProcessSteps.map((step) => ({ ...step, status: "completed" })));
    elements.auditStatus.textContent = data.audit_enabled || data.audit?.enabled ? "有効" : elements.auditStatus.textContent;
    elements.modelStatus.textContent = firstValue(data.model, elements.modelStatus.textContent);
    if (data.run_id) setGlobalStatus(`処理が完了しました。run ID: ${data.run_id}`);
  } finally {
    elements.answerRegion.setAttribute("aria-busy", "false");
  }
}

elements.askForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = elements.question.value.trim();
  if (!question) return;
  setBusy(elements.askButton, true, "処理中…");
  try {
    await askQuestion(question);
  } catch (error) {
    showAskFailure(error.message);
  } finally {
    setBusy(elements.askButton, false);
  }
});

function normalizeRevisions(data) {
  return asArray(firstValue(data.revisions, data.items, data.results, data));
}

function revisionId(revision) {
  return String(firstValue(revision.id, revision.revision_id, ""));
}

function renderRevisionOptions() {
  const current = elements.comparisonRevision.value;
  elements.comparisonRevision.innerHTML = [
    '<option value="">改訂を選択</option>',
    ...state.revisions.map((revision) => {
      const id = revisionId(revision);
      const label = firstValue(revision.change_summary, revision.summary, revision.title, id);
      return `<option value="${escapeHtml(id)}">${escapeHtml(label)} (${escapeHtml(id)})</option>`;
    }),
  ].join("");
  if (state.revisions.some((revision) => revisionId(revision) === current)) {
    elements.comparisonRevision.value = current;
  }
}

function renderRevisions() {
  if (!state.revisions.length) {
    elements.revisionList.innerHTML = '<p class="empty-message">保存済みの改訂はありません。</p>';
    renderRevisionOptions();
    return;
  }
  elements.revisionList.innerHTML = state.revisions
    .map((revision) => {
      const id = revisionId(revision);
      const status = firstValue(revision.status, revision.state, "draft");
      const sourceUrl = safeUrl(firstValue(revision.source_url, revision.source));
      const sourceMarkup = sourceUrl
        ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">原典を開く</a>`
        : "<span>原典URLなし</span>";
      const rejectable = !["active", "approved", "rejected"].includes(String(status).toLowerCase());
      return `
        <article class="revision-row">
          <div class="row-heading">
            <div>
              <strong>${escapeHtml(firstValue(revision.change_summary, revision.summary, "変更概要なし"))}</strong>
              <span>ID ${escapeHtml(id)}</span>
            </div>
            <span class="state-badge state-idle">${escapeHtml(status)}</span>
          </div>
          <dl class="inline-data">
            <div><dt>文書</dt><dd>${escapeHtml(firstValue(revision.title, revision.source_title, "—"))}</dd></div>
            <div><dt>操作者</dt><dd>${escapeHtml(firstValue(revision.operator, revision.created_by, "—"))}</dd></div>
            <div><dt>作成</dt><dd>${escapeHtml(formatDate(firstValue(revision.created_at, revision.created)))}</dd></div>
          </dl>
          <div class="row-actions">
            ${sourceMarkup}
            ${
              rejectable
                ? `<button class="button button-danger button-compact" type="button" data-reject-revision="${escapeHtml(id)}"><span aria-hidden="true">×</span> 却下</button>`
                : ""
            }
          </div>
        </article>
      `;
    })
    .join("");
  renderRevisionOptions();
}

async function loadRevisions() {
  elements.revisionList.setAttribute("aria-busy", "true");
  try {
    const data = await apiFetch("/workbench/revisions");
    state.revisions = normalizeRevisions(data);
    renderRevisions();
  } catch (error) {
    elements.revisionList.innerHTML = `<p class="empty-message error-message">${escapeHtml(error.message)}</p>`;
    state.revisions = [];
    renderRevisionOptions();
  } finally {
    elements.revisionList.setAttribute("aria-busy", "false");
  }
}

elements.refreshRevisions.addEventListener("click", loadRevisions);

elements.revisionFile.addEventListener("change", async () => {
  const file = elements.revisionFile.files[0];
  if (!file) return;
  const extension = file.name.split(".").pop().toLowerCase();
  if (extension === "md" || extension === "txt") {
    elements.revisionContent.value = await file.text();
    elements.revisionFormStatus.textContent = `${file.name} を編集欄へ読み込みました。`;
  } else {
    elements.revisionContent.value = "";
    elements.revisionFormStatus.textContent = `${file.name} をPDF改訂版として保存します。`;
  }
  elements.revisionTitle.value ||= file.name.replace(/\.[^.]+$/, "");
});

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

elements.revisionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = elements.revisionFile.files[0];
  const payload = {
    operator: elements.revisionOperator.value.trim(),
    change_summary: elements.revisionSummary.value.trim(),
    title: elements.revisionTitle.value.trim(),
    source_url: elements.revisionSourceUrl.value.trim() || null,
  };
  if (file && file.name.toLowerCase().endsWith(".pdf")) {
    payload.filename = file.name;
    payload.content_base64 = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
  } else {
    payload.content = elements.revisionContent.value;
  }
  if (!payload.content && !payload.content_base64) {
    elements.revisionFormStatus.textContent = "文書内容を入力するか、PDFを選択してください。";
    return;
  }
  setBusy(elements.createRevision, true, "作成中…");
  elements.revisionFormStatus.textContent = "";
  try {
    const data = await apiFetch("/workbench/revisions", { method: "POST", body: JSON.stringify(payload) });
    elements.revisionFormStatus.textContent = `ドラフトを作成しました。ID: ${firstValue(data.id, data.revision_id, "取得中")}`;
    elements.revisionSummary.value = "";
    elements.revisionFile.value = "";
    await loadRevisions();
  } catch (error) {
    elements.revisionFormStatus.textContent = `作成できませんでした: ${error.message}`;
  } finally {
    setBusy(elements.createRevision, false);
  }
});

elements.revisionList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-reject-revision]");
  if (!button) return;
  const id = button.dataset.rejectRevision;
  setBusy(button, true, "却下中…");
  try {
    await apiFetch(`/workbench/revisions/${encodeURIComponent(id)}/reject`, {
      method: "POST",
      body: JSON.stringify({
        operator: elements.revisionOperator.value.trim() || "workbench-operator",
        reason: "ワークベンチから却下",
      }),
    });
    setGlobalStatus(`改訂 ${id} を却下しました。`);
    await loadRevisions();
  } catch (error) {
    setGlobalStatus(`改訂を却下できませんでした: ${error.message}`);
    setBusy(button, false);
  }
});

function jobId(job) {
  return String(firstValue(job.id, job.job_id, ""));
}

function unwrapJob(data) {
  return firstValue(data.job, data.result?.job, data);
}

function jobStatus(job) {
  return String(firstValue(job.status, job.state, "queued"));
}

function isTerminalJob(job) {
  return ["completed", "complete", "succeeded", "success", "failed", "error", "cancelled"].includes(jobStatus(job).toLowerCase());
}

function backendAllowsApproval(job) {
  return firstValue(
    job.approve_permitted,
    job.approval_allowed,
    job.can_approve,
    job.approval?.permitted,
    job.approval?.allowed,
    job.result?.approve_permitted,
    job.result?.approval_allowed,
    job.result?.can_approve,
    job.result?.approval?.permitted,
    job.result?.approval?.allowed,
    false,
  ) === true;
}

function renderJobs() {
  const jobs = Array.from(state.jobs.values());
  if (!jobs.length) {
    elements.jobList.innerHTML = '<p class="empty-message">この画面で開始したジョブがここに表示されます。</p>';
    return;
  }
  elements.jobList.innerHTML = jobs
    .map((job) => {
      const id = jobId(job);
      const type = firstValue(job.type, job.job_type, job.kind, "検証");
      const status = jobStatus(job);
      const progress = firstValue(job.progress, job.progress_percent);
      return `
        <article class="job-row">
          <div>
            <strong>${escapeHtml(type)}</strong>
            <span>Job ${escapeHtml(id)}</span>
          </div>
          <div class="job-state">
            ${progress !== undefined && progress !== null ? `<span>${escapeHtml(progress)}%</span>` : ""}
            <span class="state-badge state-idle">${escapeHtml(status)}</span>
            <button class="button button-secondary button-compact" type="button" data-refresh-job="${escapeHtml(id)}">
              <span aria-hidden="true">↻</span> 更新
            </button>
          </div>
        </article>
      `;
    })
    .join("");
}

function comparisonSide(label, value) {
  const data = value && typeof value === "object" ? value : { answer: value };
  const items = asArray(data.items);
  const displayedItems = items.length ? items : [data];
  const summary = data.total !== undefined
    ? `<p class="comparison-summary">公開 ${escapeHtml(data.released)} / 全 ${escapeHtml(data.total)} · 停止 ${escapeHtml(data.blocked)} · エラー ${escapeHtml(data.errors)}</p>`
    : "";
  return `
    <section class="comparison-side">
      <h3>${escapeHtml(label)}</h3>
      ${summary}
      <div class="comparison-items">
        ${displayedItems.map((item) => {
          const verification = item.verification || {};
          const axes = verification.axes || item.axes || {};
          return `
            <article class="comparison-item">
              <strong>${escapeHtml(firstValue(item.question, item.id, "評価項目"))}</strong>
              <div class="comparison-answer">${escapeHtml(firstValue(item.answer, item.output, item.text, item.blocked_reason, "回答なし"))}</div>
              <dl class="inline-data">
                <div><dt>総合</dt><dd>${escapeHtml(scoreValue(firstValue(verification.status, item.verification_status)))}</dd></div>
                <div><dt>忠実性</dt><dd>${escapeHtml(scoreValue(firstValue(verification.faithfulness, axes.faithfulness)))}</dd></div>
                <div><dt>関連性</dt><dd>${escapeHtml(scoreValue(firstValue(verification.relevance, axes.relevance)))}</dd></div>
                <div><dt>誤情報なし</dt><dd>${escapeHtml(scoreValue(firstValue(verification.no_misinfo, axes.no_misinfo)))}</dd></div>
              </dl>
            </article>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function renderComparison(job) {
  const result = job.result || job.comparison || job.output || {};
  const before = firstValue(result.before, result.baseline, job.before);
  const after = firstValue(result.after, result.candidate, job.after);
  const selectedRevision = String(firstValue(job.revision_id, result.revision_id, elements.comparisonRevision.value));
  state.approval = { allowed: backendAllowsApproval(job), revisionId: selectedRevision };
  elements.approveRevision.disabled = !state.approval.allowed;
  elements.approvalNote.textContent = state.approval.allowed
    ? "バックエンドの検証条件を満たしました。内容を確認して承認できます。"
    : firstValue(job.approval_blocked_reason, result.approval_blocked_reason, "バックエンドの検証結果が承認可能になるまで、承認操作は無効です。");

  if (before === undefined && after === undefined) return;
  elements.comparisonResult.innerHTML =
    comparisonSide("変更前", before) + comparisonSide("変更後", after);
}

async function refreshJob(id, schedule = true) {
  try {
    const job = unwrapJob(await apiFetch(`/workbench/jobs/${encodeURIComponent(id)}`));
    state.jobs.set(id, job);
    renderJobs();
    renderComparison(job);
    if (schedule && !isTerminalJob(job)) {
      clearTimeout(state.pollTimers.get(id));
      state.pollTimers.set(id, window.setTimeout(() => refreshJob(id), 2000));
    } else {
      clearTimeout(state.pollTimers.get(id));
      state.pollTimers.delete(id);
    }
  } catch (error) {
    const current = state.jobs.get(id) || { id };
    state.jobs.set(id, { ...current, status: "poll_error", message: error.message });
    renderJobs();
    clearTimeout(state.pollTimers.get(id));
    state.pollTimers.delete(id);
  }
}

async function startJob(kind) {
  const revisionIdValue = elements.comparisonRevision.value;
  const question = elements.comparisonQuestion.value.trim();
  if (!revisionIdValue) {
    elements.comparisonFormStatus.textContent = "改訂を選択してください。";
    elements.comparisonRevision.focus();
    return;
  }
  if (kind === "comparison" && !question) {
    elements.comparisonFormStatus.textContent = "比較する質問を入力してください。";
    elements.comparisonQuestion.focus();
    return;
  }
  const button = kind === "comparison" ? elements.startComparison : elements.startValidation;
  setBusy(button, true, "開始中…");
  elements.comparisonFormStatus.textContent = "";
  const endpoint = kind === "comparison" ? "comparison-jobs" : "validation-jobs";
  try {
    const job = unwrapJob(
      await apiFetch(`/workbench/revisions/${encodeURIComponent(revisionIdValue)}/${endpoint}`, {
        method: "POST",
        body: JSON.stringify(kind === "comparison" ? { question } : {}),
      }),
    );
    const id = jobId(job);
    if (!id) throw new Error("ジョブIDが返されませんでした。");
    state.jobs.set(id, { ...job, revision_id: firstValue(job.revision_id, revisionIdValue), type: firstValue(job.type, kind) });
    renderJobs();
    elements.comparisonFormStatus.textContent = `ジョブ ${id} を開始しました。`;
    refreshJob(id);
  } catch (error) {
    elements.comparisonFormStatus.textContent = `ジョブを開始できませんでした: ${error.message}`;
  } finally {
    setBusy(button, false);
  }
}

elements.comparisonForm.addEventListener("submit", (event) => {
  event.preventDefault();
  startJob("comparison");
});
elements.startValidation.addEventListener("click", () => startJob("validation"));

elements.jobList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-refresh-job]");
  if (button) refreshJob(button.dataset.refreshJob, false);
});

elements.approveRevision.addEventListener("click", async () => {
  if (!state.approval.allowed || !state.approval.revisionId) return;
  setBusy(elements.approveRevision, true, "承認中…");
  try {
    await apiFetch(`/workbench/revisions/${encodeURIComponent(state.approval.revisionId)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        operator: elements.revisionOperator.value.trim() || "workbench-operator",
        reason: "全件検証と比較結果を確認して承認",
      }),
    });
    state.approval.allowed = false;
    elements.approveRevision.disabled = true;
    elements.approvalNote.textContent = "承認しました。台帳で反映記録を確認できます。";
    setGlobalStatus(`改訂 ${state.approval.revisionId} を承認しました。`);
    await loadRevisions();
  } catch (error) {
    elements.approvalNote.textContent = `承認できませんでした: ${error.message}`;
    setBusy(elements.approveRevision, false);
  }
});

function normalizeAdjustments(data) {
  return asArray(firstValue(data.adjustments, data.items, data.results, data));
}

function traceButton(traceId, traceUrl) {
  if (traceId) {
    const knownUrl = safeUrl(traceUrl);
    return `<button class="button button-secondary button-compact" type="button" data-trace-id="${escapeHtml(traceId)}"${knownUrl ? ` data-trace-url="${escapeHtml(knownUrl)}"` : ""}>→ 着弾を確認</button>`;
  }
  return '<span class="muted-text">トレースなし</span>';
}

function renderLedger(items) {
  if (!items.length) {
    elements.ledgerSummary.innerHTML = '<p class="empty-message">台帳レコードはありません。</p>';
    return;
  }
  elements.ledgerSummary.innerHTML = items
    .map((item) => {
      const adjustment = item.adjustment || item;
      const event = item.event || adjustment.event || {};
      const run = item.run || adjustment.run || {};
      const audit = item.audit || adjustment.audit || {};
      const traceId = firstValue(audit.trace_id, run.trace_id, item.trace_id);
      const traceUrl = firstValue(audit.trace_url, run.trace_url, item.trace_url);
      const beforeAnswers = asArray(item.before_answers).filter(Boolean).join("\n");
      const afterAnswers = asArray(item.after_answers).filter(Boolean).join("\n");
      const sideEffects = asArray(item.side_effects);
      return `
        <article class="ledger-row">
          <div class="row-heading">
            <div>
              <strong>${escapeHtml(firstValue(adjustment.change_summary, adjustment.summary, adjustment.title, "調整記録"))}</strong>
              <span>${escapeHtml(formatDate(firstValue(adjustment.created_at, event.created_at, item.created_at)))}</span>
            </div>
            <span class="state-badge state-idle">${escapeHtml(firstValue(event.type, event.status, adjustment.status, "記録済み"))}</span>
          </div>
          <dl class="ledger-data">
            <div><dt>調整</dt><dd>${escapeHtml(firstValue(adjustment.id, adjustment.adjustment_id, adjustment.revision_id, "—"))}</dd></div>
            <div><dt>イベント</dt><dd>${escapeHtml(firstValue(event.id, event.event_id, event.type, "—"))}</dd></div>
            <div><dt>実行</dt><dd>${escapeHtml(firstValue(run.id, run.run_id, item.run_id, "—"))}</dd></div>
            <div><dt>監査</dt><dd>${escapeHtml(firstValue(audit.status, item.trace_status, traceId ? "記録あり" : "記録なし"))}</dd></div>
          </dl>
          <details class="ledger-details">
            <summary>判断材料を表示</summary>
            <dl class="ledger-data">
              <div><dt>質問</dt><dd>${escapeHtml(firstValue(item.question, "—"))}</dd></div>
              <div><dt>問題工程</dt><dd>${escapeHtml(firstValue(item.problem_stage, "—"))}</dd></div>
              <div><dt>原因分類</dt><dd>${escapeHtml(firstValue(item.cause_category, "—"))}</dd></div>
              <div><dt>変更内容</dt><dd>${escapeHtml(firstValue(item.change_content, adjustment.summary, "—"))}</dd></div>
              <div><dt>変更前SHA</dt><dd><code>${escapeHtml(firstValue(item.knowledge_sha_before, "—"))}</code></dd></div>
              <div><dt>変更後SHA</dt><dd><code>${escapeHtml(firstValue(item.knowledge_sha_after, "—"))}</code></dd></div>
              <div><dt>判断</dt><dd>${escapeHtml(firstValue(item.decision, "保留"))}</dd></div>
              <div><dt>判断理由</dt><dd>${escapeHtml(firstValue(item.decision_reason, "—"))}</dd></div>
              <div><dt>操作者</dt><dd>${escapeHtml(firstValue(item.operator, "—"))}</dd></div>
              <div><dt>副作用</dt><dd>${escapeHtml(sideEffects.length ? sideEffects.join(", ") : "なし")}</dd></div>
            </dl>
            ${beforeAnswers ? `<h4>変更前回答</h4><p class="ledger-answer">${escapeHtml(beforeAnswers)}</p>` : ""}
            ${afterAnswers ? `<h4>変更後回答</h4><p class="ledger-answer">${escapeHtml(afterAnswers)}</p>` : ""}
          </details>
          <div class="row-actions">${traceButton(traceId, traceUrl)}</div>
        </article>
      `;
    })
    .join("");
}

async function loadLedger() {
  elements.ledgerSummary.setAttribute("aria-busy", "true");
  try {
    const data = await apiFetch("/workbench/adjustments");
    renderLedger(normalizeAdjustments(data));
  } catch (error) {
    elements.ledgerSummary.innerHTML = `<p class="empty-message error-message">${escapeHtml(error.message)}</p>`;
  } finally {
    elements.ledgerSummary.setAttribute("aria-busy", "false");
  }
}

elements.refreshLedger.addEventListener("click", loadLedger);

async function pollTrace(traceId, deadlineMs = 60000) {
  const deadline = Date.now() + deadlineMs;
  let data = await apiFetch(`/audit/traces/${encodeURIComponent(traceId)}`);
  while (data.status === "pending" && Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
    data = await apiFetch(`/audit/traces/${encodeURIComponent(traceId)}`);
  }
  return data;
}

elements.ledgerSummary.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-trace-id]");
  if (!button) return;
  const traceId = button.dataset.traceId;
  setBusy(button, true, "取得中…");
  elements.traceDetail.setAttribute("aria-busy", "true");
  try {
    elements.traceDetail.innerHTML = '<p class="empty-message">Langfuseへの着弾を確認しています。</p>';
    const data = await pollTrace(traceId);
    const links = asArray(firstValue(data.links, data.trace_links));
    const traceUrl = safeUrl(firstValue(data.trace_url, button.dataset.traceUrl));
    const traceLink = traceUrl
      ? `<li><a href="${escapeHtml(traceUrl)}" target="_blank" rel="noreferrer">Langfuse traceを開く</a></li>`
      : "";
    const additionalLinks = links
      .map((link) => {
        const href = safeUrl(firstValue(link.url, link.href));
        return href
          ? `<li><a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${escapeHtml(firstValue(link.label, link.title, href))}</a></li>`
          : "";
      })
      .join("");
    const linkMarkup = `${traceLink}${additionalLinks}`;
    elements.traceDetail.innerHTML = `
      <dl class="ledger-data">
        <div><dt>Trace ID</dt><dd>${escapeHtml(firstValue(data.trace_id, data.id, traceId))}</dd></div>
        <div><dt>状態</dt><dd>${escapeHtml(firstValue(data.status, data.trace_status, "取得済み"))}</dd></div>
        <div><dt>送信状態</dt><dd>${escapeHtml(firstValue(data.local_status, "—"))}</dd></div>
        <div><dt>Run ID</dt><dd>${escapeHtml(firstValue(data.run_id, data.run?.id, "—"))}</dd></div>
        <div><dt>工程</dt><dd>${escapeHtml(asArray(data.observation_names).join(" → ") || "—")}</dd></div>
        <div><dt>概要</dt><dd>${escapeHtml(firstValue(data.summary, data.message, "—"))}</dd></div>
      </dl>
      ${linkMarkup ? `<ul class="trace-links">${linkMarkup}</ul>` : ""}
    `;
  } catch (error) {
    elements.traceDetail.innerHTML = `<p class="empty-message error-message">${escapeHtml(error.message)}</p>`;
  } finally {
    elements.traceDetail.setAttribute("aria-busy", "false");
    setBusy(button, false);
  }
});

window.addEventListener("beforeunload", () => {
  state.pollTimers.forEach((timer) => clearTimeout(timer));
});

loadHealth();
