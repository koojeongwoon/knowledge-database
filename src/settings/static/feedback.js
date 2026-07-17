const style = document.createElement("link");
style.rel = "stylesheet";
style.href = "/settings/assets/feedback.css";
document.head.append(style);

const byId = id => document.getElementById(id);
const headers = {"Content-Type": "application/json"};
const failures = [
  ["missing_answer", "정답 문서 누락"], ["irrelevant_results", "무관 문서 노출"],
  ["wrong_order", "순위가 잘못됨"], ["insufficient_content", "내용 부족"],
  ["intent_mismatch", "질문 의도 오해"], ["no_knowledge", "지식베이스에 답 없음"],
];
const resultIssues = [
  ["outdated", "오래된 정보"], ["superseded", "더 최신 문서가 있음"],
  ["wrong_relation", "잘못 연결됨"], ["contradictory", "다른 지식과 충돌"],
  ["unsafe", "노출되면 안 됨"], ["insufficient", "내용 부족"],
  ["unrelated", "질문과 무관"],
];

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
function radio(name, value, label, checked = false) {
  const wrap = el("label");
  const input = el("input");
  input.type = "radio";
  input.name = name;
  input.value = value;
  input.checked = checked;
  wrap.append(input, document.createTextNode(` ${label}`));
  return wrap;
}
function checkbox(value, label, checked, role) {
  const wrap = el("label");
  const input = el("input");
  input.type = "checkbox";
  input.value = value;
  input.checked = checked;
  input.dataset.role = role;
  wrap.append(input, document.createTextNode(` ${label}`));
  return wrap;
}
function existingFeedback(event, path) {
  return (event.result_feedback || []).find(item => item.file_path === path) || null;
}
function legacyGrade(event, path) {
  if ((event.relevant_paths || []).includes(path)) return 3;
  if ((event.partially_relevant_paths || []).includes(path)) return 2;
  if ((event.irrelevant_paths || []).includes(path)) return 0;
  return null;
}

function renderResult(event, result, index) {
  const feedback = existingFeedback(event, result.file_path);
  const grade = feedback ? feedback.relevance_grade : legacyGrade(event, result.file_path);
  const row = el("div", "review-path");
  row.dataset.path = result.file_path;
  row.dataset.rank = result.rank || index + 1;
  row.append(
    el("code", "path-title", `${result.rank || "보조"}. ${result.file_path}`),
    el("span", "score-line", `vector ${Number(result.vector_similarity || 0).toFixed(3)} · lexical ${Number(result.lexical_rank || 0).toFixed(3)} · RRF ${Number(result.rrf_score || 0).toFixed(3)} · ${result.retrieval_kind || "direct"}`),
  );

  const grades = el("div", "label-options grade-options");
  const name = `grade-${event.search_id}-${index}`;
  [[3, "매우 도움"], [2, "부분 도움"], [1, "약간 관련"], [0, "무관"], ["", "미평가"]]
    .forEach(([value, label]) => grades.append(radio(name, String(value), label, grade === value || (value === "" && grade === null))));
  row.append(el("h4", "field-label", "이 문서가 얼마나 도움이 됐나요?"), grades);

  const issues = el("div", "result-issue-options");
  resultIssues.forEach(([value, label]) => issues.append(checkbox(
    value, label, (feedback?.issue_reasons || []).includes(value), "result-issue",
  )));
  row.append(el("h4", "field-label", "문제가 있었다면 선택해 주세요"), issues);

  const replacement = el("input");
  replacement.dataset.role = "replacement";
  replacement.placeholder = "더 적절한 문서 경로 (선택)";
  replacement.value = feedback?.preferred_replacement_path || "";
  row.append(replacement);

  if ((result.retrieval_kind || "direct") === "graph") {
    const relation = el("div", "relation-feedback");
    relation.append(el("span", "field-label", "이 관계를 따라온 결과가 도움 됐나요?"));
    const relationName = `relation-${event.search_id}-${index}`;
    relation.append(
      radio(relationName, "true", "예", feedback?.relation_helpful === true),
      radio(relationName, "false", "아니요", feedback?.relation_helpful === false),
      radio(relationName, "", "모르겠음", feedback?.relation_helpful == null),
    );
    row.append(relation);
  }
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
    card.append(
      el("p", "review-query", event.query_text),
      el("p", "review-meta", `${new Date(event.created_at).toLocaleString()} · ${event.pipeline_version} · ranking ${event.ranking_config_version || "retrieval-v1"} · ontology ${event.ontology_version || "none"}`),
    );
    const paths = el("div", "review-paths");
    (event.returned_results || []).forEach((result, index) => paths.append(renderResult(event, result, index)));
    card.append(paths);

    const overall = el("div", "evaluation-block");
    overall.append(el("h3", "", "전체 만족도"));
    const sats = el("div", "satisfaction");
    sats.append(
      radio(`sat-${event.search_id}`, "satisfied", "만족", event.satisfaction === "satisfied"),
      radio(`sat-${event.search_id}`, "partial", "부분 만족", event.satisfaction === "partial"),
      radio(`sat-${event.search_id}`, "dissatisfied", "불만족", event.satisfaction === "dissatisfied"),
    );
    overall.append(sats);
    card.append(overall);

    const why = el("div", "evaluation-block");
    why.append(el("h3", "", "검색 전체의 문제 (복수 선택)"));
    const failureBox = el("div", "failure-options");
    failures.forEach(([value, label]) => failureBox.append(checkbox(
      value, label, (event.failure_reasons || []).includes(value), "failure",
    )));
    why.append(failureBox);
    card.append(why);

    const noAnswer = el("label", "no-answer-option");
    const no = el("input");
    no.type = "checkbox";
    no.dataset.role = "no-answer";
    no.checked = !!event.expected_no_answer;
    noAnswer.append(no, document.createTextNode(" 지식베이스에 정답이 없음"));
    card.append(noAnswer);

    const missing = el("input");
    missing.dataset.role = "missing";
    missing.placeholder = "누락된 정답 문서 경로 (선택)";
    missing.value = event.missing_answer_path || "";
    const notes = el("textarea");
    notes.dataset.role = "notes";
    notes.placeholder = "검색 전체에 대한 검수 메모 (선택)";
    notes.value = event.notes || "";
    const save = el("button", "secondary", event.labeled_at ? "평가 수정" : "평가 저장");
    save.type = "button";
    save.addEventListener("click", () => saveEvent(card, save));
    card.append(missing, notes, save);
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
  card.querySelectorAll(".review-path").forEach(row => {
    const rawGrade = row.querySelector("input[name^=grade-]:checked")?.value ?? "";
    if (rawGrade === "") return;
    const grade = Number(rawGrade);
    if (grade === 3) groups.relevant_paths.push(row.dataset.path);
    if (grade === 2 || grade === 1) groups.partially_relevant_paths.push(row.dataset.path);
    if (grade === 0) groups.irrelevant_paths.push(row.dataset.path);
    const relationValue = row.querySelector("input[name^=relation-]:checked")?.value;
    resultFeedback.push({
      file_path: row.dataset.path,
      relevance_grade: grade,
      issue_reasons: [...row.querySelectorAll("[data-role=result-issue]:checked")].map(node => node.value),
      preferred_replacement_path: row.querySelector("[data-role=replacement]").value || null,
      relation_helpful: relationValue === "true" ? true : relationValue === "false" ? false : null,
    });
  });
  const payload = {
    ...groups,
    result_feedback: resultFeedback,
    satisfaction: card.querySelector("input[name^=sat-]:checked")?.value || null,
    failure_reasons: [...card.querySelectorAll("[data-role=failure]:checked")].map(node => node.value),
    expected_no_answer: card.querySelector("[data-role=no-answer]").checked,
    missing_answer_path: card.querySelector("[data-role=missing]").value || null,
    notes: card.querySelector("[data-role=notes]").value || null,
  };
  try {
    const response = await fetch(`/api/search-feedback/${card.dataset.searchId}`, {
      method: "PUT", headers, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "평가를 저장하지 못했습니다.");
    button.textContent = "평가 수정";
    msg("평가를 저장했습니다.");
  } catch (error) { msg(error.message, true); }
  finally { button.disabled = false; }
}

byId("refresh-feedback").addEventListener("click", load);
load();
