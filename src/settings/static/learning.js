const $ = (selector) => document.querySelector(selector);
const labels = {
  mastered: "충분히 설명", partial: "부분 이해", misconception: "오개념", unknown: "모름", unverifiable: "근거 부족",
  pending: "승인 대기", approved: "승인", rejected: "거절", committing: "저장 중", committed: "지식 저장",
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderSummary(data) {
  const values = [
    ["진행 중 세션", data.sessions.active_sessions || 0, `전체 ${data.sessions.total_sessions || 0}개`],
    ["복습할 항목", data.reviews.due_reviews || 0, `예정 ${data.reviews.scheduled_reviews || 0}개`],
    ["기간 내 복습", data.reviews.review_attempts_period || 0, `최근 ${data.period_days}일`],
    ["지식으로 저장", data.knowledge_candidates.committed || 0, `승인 대기 ${data.knowledge_candidates.pending || 0}개`],
  ];
  const target = $("#summary"); target.replaceChildren();
  values.forEach(([title, value, detail]) => {
    const card = node("article", "learning-stat");
    card.append(node("span", "", title), node("strong", "", String(value)), node("small", "", detail));
    target.append(card);
  });
}

function renderMetrics(selector, values) {
  const target = $(selector); target.replaceChildren();
  const max = Math.max(1, ...Object.values(values));
  Object.entries(values).forEach(([key, value]) => {
    const row = node("div", "metric-row");
    const track = node("div", "metric-track");
    const fill = node("div", "metric-fill"); fill.style.width = `${Math.round(value / max * 100)}%`; track.append(fill);
    row.append(node("span", "", labels[key] || key), track, node("span", "metric-value", String(value)));
    target.append(row);
  });
}

function renderTopics(topics) {
  const target = $("#topics"); target.replaceChildren();
  if (!topics.length) { target.append(node("div", "empty-learning", "아직 학습한 주제가 없습니다.")); return; }
  const head = node("div", "table-row table-head");
  ["주제", "세션", "답변", "오개념 판정"].forEach(value => head.append(node("span", "", value))); target.append(head);
  topics.forEach(topic => {
    const row = node("div", "table-row");
    row.append(node("strong", "", topic.topic), node("span", "", String(topic.session_count)), node("span", "", String(topic.attempt_count)), node("span", "", String(topic.misconception_labels)));
    target.append(row);
  });
}

function renderSessions(sessions) {
  const target = $("#sessions"); target.replaceChildren();
  if (!sessions.length) { target.append(node("div", "empty-learning", "아직 저장된 학습 세션이 없습니다.")); return; }
  sessions.forEach(session => {
    const item = node("article", "session-item"); const body = node("div");
    body.append(node("strong", "", session.topic));
    const meta = node("div", "session-meta");
    meta.append(node("span", "", session.effective_scope), node("span", "", `질문 ${session.question_count}`), node("span", "", `답변 ${session.attempt_count}`)); body.append(meta);
    item.append(body, node("span", `status-chip ${session.status}`, session.status === "completed" ? "완료" : "진행 중")); target.append(item);
  });
}

async function load() {
  $("#message").textContent = "";
  try {
    const response = await fetch(`/api/learning/dashboard?days=${encodeURIComponent($("#period").value)}`);
    if (response.status === 401) { location.href = "/login"; return; }
    if (!response.ok) throw new Error((await response.json()).detail || "학습 현황을 불러오지 못했습니다.");
    const data = await response.json();
    renderSummary(data); renderMetrics("#assessments", data.client_llm_assessments); renderMetrics("#candidates", data.knowledge_candidates); renderTopics(data.topics); renderSessions(data.recent_sessions);
    $("#metric-notice").textContent = data.metric_contract.notice;
  } catch (error) { $("#message").textContent = error.message; }
}

$("#refresh").addEventListener("click", load);
$("#period").addEventListener("change", load);
load();
