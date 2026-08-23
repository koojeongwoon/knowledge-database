const style = document.createElement("link");
style.rel = "stylesheet";
style.href = "/settings/assets/feedback.css";
document.head.append(style);

const byId = id => document.getElementById(id);
const headers = {"Content-Type": "application/json"};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function msg(text, error = false) {
  const node = byId("feedback-message");
  node.textContent = text;
  node.className = `message${error ? " error" : ""}`;
}

function highlightQuery(text, query) {
  if (!text) return "";
  if (!query) return text;
  const words = query.split(/\s+/).filter(w => w.length > 1);
  if (!words.length) return text;
  const escaped = words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const regex = new RegExp(`(${escaped})`, "gi");
  return text.replace(regex, "<mark>$1</mark>");
}

function existingFeedback(event, path) {
  return (event.result_feedback || []).find(item => item.file_path === path) || null;
}

function determineBadge(result) {
  const sources = result.search_sources || [];
  const kind = result.retrieval_kind || "direct";
  if (kind === "graph") return { text: "연관 지식 확장 (Graph)", cls: "badge-graph" };
  if (sources.includes("vector") && sources.includes("keyword")) {
    return { text: "복합 적중 (단어+의미)", cls: "badge-both" };
  }
  if (sources.includes("vector")) return { text: "문맥/의미 유사 (Vector)", cls: "badge-vector" };
  if (sources.includes("keyword")) return { text: "단어 일치 (Keyword)", cls: "badge-lexical" };
  return { text: "검색 적중", cls: "badge-lexical" };
}

function renderResult(event, result, index) {
  const feedback = existingFeedback(event, result.file_path);
  const grade = feedback ? feedback.relevance_grade : null;
  const issueReasons = feedback?.issue_reasons || [];
  const isOutdated = issueReasons.includes("outdated") || issueReasons.includes("superseded");

  const row = el("div", "review-path");
  row.dataset.path = result.file_path;
  row.dataset.rank = result.rank || index + 1;

  // Header: Path + Source Badge + Full View Link
  const header = el("div", "path-header");
  const title = el("a", "path-title doc-link", `${result.rank || index + 1}. ${result.file_path}`);
  title.target = "_blank";
  const snippetText = (result.matched_chunk_preview || "").slice(0, 80);
  title.href = `/documents?path=${encodeURIComponent(result.file_path)}&highlight=${encodeURIComponent(snippetText)}&q=${encodeURIComponent(event.query_text)}`;
  title.title = "새 탭에서 문서 전문 열기 (적중 위치로 이동)";

  const badgeInfo = determineBadge(result);
  const badge = el("span", `source-badge ${badgeInfo.cls}`, badgeInfo.text);
  header.append(title, badge);
  row.append(header);

  // Match Preview Snippet with Query Highlight
  const preview = result.matched_chunk_preview || "";
  if (preview) {
    const snippet = el("div", "match-snippet");
    snippet.innerHTML = highlightQuery(preview, event.query_text);
    row.append(snippet);
  }

  // Quick 1-Touch Action Buttons
  const evalRow = el("div", "quick-eval-row");
  evalRow.append(el("span", "quick-eval-label", "문서 판정:"));

  const btnGroup = el("div", "eval-btn-group");
  const fieldName = `eval-type-${event.search_id}-${index}`;

  const options = [
    { val: "match", label: "🎯 정확함 (정답)", cls: "chip-match", checked: grade === 3 && !isOutdated },
    { val: "partial", label: "🤏 부분 일치 (참고)", cls: "chip-partial", checked: (grade === 2 || grade === 1) && !isOutdated },
    { val: "outdated", label: "⏰ 오래됨/구버전", cls: "chip-outdated", checked: isOutdated },
    { val: "irrelevant", label: "❌ 전혀 아님 (오답)", cls: "chip-irrelevant", checked: grade === 0 },
  ];

  options.forEach(opt => {
    const label = el("label", `eval-chip ${opt.cls}`);
    const radio = el("input");
    radio.type = "radio";
    radio.name = fieldName;
    radio.value = opt.val;
    radio.checked = opt.checked;
    label.append(radio, document.createTextNode(opt.label));
    btnGroup.append(label);
  });
  evalRow.append(btnGroup);
  row.append(evalRow);

  // Short Note / Replacement input
  const noteInput = el("input", "eval-comment-input");
  noteInput.dataset.role = "result-notes";
  noteInput.placeholder = "메모 또는 정답 문서 경로가 있다면 입력 (선택)";
  noteInput.value = feedback?.notes || feedback?.preferred_replacement_path || "";
  row.append(noteInput);

  return row;
}

function render(events) {
  const root = byId("search-events");
  root.replaceChildren();
  if (!events.length) {
    root.append(el("p", "hint", "평가할 검색 기록이 없습니다."));
    return;
  }

  events.forEach(event => {
    const card = el("article", "review-item");
    card.dataset.searchId = event.search_id;

    // Header
    const cardHeader = el("div", "review-header");
    const queryWrap = el("div", "review-query-wrap");
    queryWrap.append(
      el("span", "review-query-badge", "검색어"),
      el("h3", "review-query", `"${event.query_text}"`),
    );
    const metaWrap = el("div");
    metaWrap.append(
      queryWrap,
      el("p", "review-meta", `${new Date(event.created_at).toLocaleString()} · ${event.result_count || (event.returned_results || []).length}개 문서 검색됨`),
    );

    const graphLink = el("a", "review-graph-link", "🕸️ 검색 그래프 보기");
    graphLink.href = `/search-feedback/${encodeURIComponent(event.search_id)}`;
    graphLink.target = "_blank";
    cardHeader.append(metaWrap, graphLink);
    card.append(cardHeader);

    // Document Results
    const paths = el("div", "review-paths");
    (event.returned_results || []).forEach((result, index) => {
      paths.append(renderResult(event, result, index));
    });
    card.append(paths);

    // Footer: Overall satisfaction & Submit
    const footer = el("div", "evaluation-footer");
    const overallRow = el("div", "overall-row");

    const satGroup = el("div", "eval-btn-group");
    const satName = `overall-sat-${event.search_id}`;
    [
      { val: "satisfied", label: "👍 전체 만족", cls: "chip-match", checked: event.satisfaction === "satisfied" },
      { val: "partial", label: "🤔 보통 / 애매함", cls: "chip-partial", checked: event.satisfaction === "partial" },
      { val: "dissatisfied", label: "👎 불만족", cls: "chip-irrelevant", checked: event.satisfaction === "dissatisfied" },
    ].forEach(opt => {
      const label = el("label", `eval-chip ${opt.cls}`);
      const radio = el("input");
      radio.type = "radio";
      radio.name = satName;
      radio.value = opt.val;
      radio.checked = opt.checked;
      label.append(radio, document.createTextNode(opt.label));
      satGroup.append(label);
    });

    const noAnswerLabel = el("label", "eval-chip");
    const noAnswerCheck = el("input");
    noAnswerCheck.type = "checkbox";
    noAnswerCheck.dataset.role = "no-answer";
    noAnswerCheck.checked = !!event.expected_no_answer;
    noAnswerLabel.append(noAnswerCheck, document.createTextNode("🚫 지식베이스에 답이 없는 질문임"));

    overallRow.append(satGroup, noAnswerLabel);
    footer.append(overallRow);

    // Actions
    const actionRow = el("div", "footer-actions");
    const saveBtn = el("button", "primary", event.labeled_at ? "평가 완료 (수정하기)" : "평가 제출하기");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", () => saveEvent(card, saveBtn));
    actionRow.append(saveBtn);
    footer.append(actionRow);

    card.append(footer);
    root.append(card);
  });
}

async function load() {
  msg("불러오는 중…");
  try {
    const response = await fetch("/api/search-feedback/events?limit=30");
    if (response.status === 401) { location.replace("/login"); return; }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "검색 기록을 불러오지 못했습니다.");
    render(data.events || []);
    msg("");
  } catch (error) { msg(error.message, true); }
}

async function saveEvent(card, button) {
  button.disabled = true;
  const groups = {relevant_paths: [], partially_relevant_paths: [], irrelevant_paths: []};
  const resultFeedback = [];
  const failureReasons = new Set();
  const expectedRuleTypes = new Set();

  card.querySelectorAll(".review-path").forEach(row => {
    const evalType = row.querySelector("input[name^=eval-type-]:checked")?.value;
    const comment = row.querySelector("[data-role=result-notes]")?.value.trim() || "";
    const path = row.dataset.path;
    if (!evalType) return;

    let grade = 1;
    const issues = [];

    if (evalType === "match") {
      grade = 3;
      groups.relevant_paths.push(path);
    } else if (evalType === "partial") {
      grade = 2;
      groups.partially_relevant_paths.push(path);
    } else if (evalType === "irrelevant") {
      grade = 0;
      issues.push("unrelated");
      groups.irrelevant_paths.push(path);
      failureReasons.add("irrelevant_results");
    } else if (evalType === "outdated") {
      grade = 1;
      issues.push("outdated");
      issues.push("superseded");
      expectedRuleTypes.add("prefer_current");
      groups.partially_relevant_paths.push(path);
    }

    resultFeedback.push({
      file_path: path,
      relevance_grade: grade,
      issue_reasons: issues,
      notes: comment || null,
      preferred_replacement_path: comment.includes(".md") ? comment : null,
      relation_helpful: null,
      ontology_context_grade: null,
      relation_path_correct: null,
      rule_application_correct: null,
    });
  });

  const satValue = card.querySelector("input[name^=overall-sat-]:checked")?.value || null;
  const isNoAnswer = !!card.querySelector("[data-role=no-answer]")?.checked;

  if (isNoAnswer) {
    failureReasons.add("no_knowledge");
    groups.relevant_paths = [];
    groups.partially_relevant_paths = [];
  }

  const payload = {
    ...groups,
    result_feedback: resultFeedback,
    satisfaction: satValue,
    failure_reasons: Array.from(failureReasons),
    expected_no_answer: isNoAnswer,
    missing_answer_path: null,
    notes: null,
    expected_relations: [],
    expected_graph_paths: [],
    forbidden_paths: [],
    expected_rule_types: Array.from(expectedRuleTypes),
    ontology_notes: null,
  };

  try {
    const response = await fetch(`/api/search-feedback/${card.dataset.searchId}`, {
      method: "PUT", headers, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "평가를 저장하지 못했습니다.");
    button.textContent = "평가 완료 (수정하기)";
    msg("평가가 성공적으로 저장되었습니다!");
  } catch (error) { msg(error.message, true); }
  finally { button.disabled = false; }
}

byId("refresh-feedback").addEventListener("click", load);
load();
