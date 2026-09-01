const byId = id => document.getElementById(id), headers = () => ({ "Content-Type": "application/json" });
function badge(id, text, good = false) { const el = byId(id); if (el) { el.textContent = text; el.className = `badge ${good ? "good" : "neutral"}`; } }
function toggleRemote() { badge("storage-state", byId("storage-type").selectedOptions[0].text, true); }
function message(text, error = false) { const el = byId("message"); el.textContent = text; el.className = `message${error ? " error" : ""}`; }

let currentAuthType = "api_key";
let pollTimer = null;

function updateAuthStateUI(authType, oauthConfigured, apiKeyConfigured) {
  currentAuthType = authType;
  byId("llm-auth-type").value = authType;

  const oauthBadge = byId("oauth-badge");
  const oauthStatus = byId("oauth-status-text");
  const loginBtn = byId("oauth-login-btn");
  const unlinkBtn = byId("oauth-unlink-btn");

  if (authType === "openai_oauth" && oauthConfigured) {
    badge("llm-state", "ChatGPT 구독 연동됨", true);
    badge("oauth-badge", "연동됨", true);
    oauthStatus.textContent = "ChatGPT Plus/Pro 구독 쿼터로 추론 실행";
    loginBtn.textContent = "다른 계정으로 재로그인";
    unlinkBtn.style.display = "inline-block";
  } else {
    badge("llm-state", apiKeyConfigured ? "API Key 모드" : "미설정", apiKeyConfigured);
    badge("oauth-badge", "미연동", false);
    oauthStatus.textContent = "API Key로 추론 실행";
    loginBtn.textContent = "ChatGPT 계정 로그인";
    unlinkBtn.style.display = "none";
  }
}

async function startOAuthFlow() {
  const btn = byId("oauth-login-btn"), guide = byId("oauth-device-guide"), statusText = byId("oauth-status-text");
  btn.disabled = true;
  statusText.textContent = "인증 코드 요청 중…";
  try {
    const res = await fetch("/api/settings/openai-oauth/device-code", { method: "POST" });
    let data;
    try {
      data = await res.json();
    } catch (e) {
      throw new Error(`서버 응답 오류 (HTTP ${res.status})`);
    }
    if (!res.ok) throw new Error(data.detail || "인증 코드를 받지 못했습니다.");

    byId("oauth-user-code").textContent = data.user_code;
    const uri = data.verification_uri_complete || data.verification_uri;
    byId("oauth-uri-link").href = uri;
    guide.style.display = "block";
    statusText.textContent = "브라우저에서 승인을 완료해 주세요.";

    window.open(uri, "_blank");

    if (pollTimer) clearInterval(pollTimer);
    const interval = (data.interval || 5) * 1000;
    pollTimer = setInterval(async () => {
      try {
        const pollRes = await fetch("/api/settings/openai-oauth/poll", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify({
            device_code: data.device_code,
            user_code: data.user_code
          })
        });
        let pollData;
        try {
          pollData = await pollRes.json();
        } catch (e) {
          return;
        }
        if (pollData.status === "complete") {
          clearInterval(pollTimer);
          guide.style.display = "none";
          btn.disabled = false;
          updateAuthStateUI("openai_oauth", true, true);
          message("✓ ChatGPT 계정 연동이 완료되었습니다! (임베딩은 상단 API Key를 사용합니다)");
        } else if (pollData.status === "pending") {
          byId("oauth-poll-message").textContent = "브라우저 승인 대기 중… (" + new Date().toLocaleTimeString() + ")";
        }
      } catch (e) {
        console.error(e);
      }
    }, interval);
  } catch (e) {
    statusText.textContent = "오류: " + e.message;
    btn.disabled = false;
  }
}

async function unlinkOAuth() {
  try {
    const res = await fetch("/api/settings/switch-auth-type", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ llm_auth_type: "api_key" })
    });
    let data;
    try {
      data = await res.json();
    } catch (e) {
      throw new Error(`전환 실패 (HTTP ${res.status})`);
    }
    if (!res.ok) throw new Error(data.detail || "전환 실패");
    updateAuthStateUI("api_key", false, true);
    message("API Key 추론 모드로 전환되었습니다.");
  } catch (e) {
    message(e.message, true);
  }
}

async function loadSettings() {
  try {
    const response = await fetch("/api/settings");
    if (response.status === 401) { location.replace("/login"); return; }
    let data;
    try {
      data = await response.json();
    } catch (e) {
      throw new Error(`설정 응답 오류 (HTTP ${response.status})`);
    }
    if (!response.ok) throw new Error(data.detail || "설정을 불러오지 못했습니다.");

    updateAuthStateUI(
      data.llm_auth_type || "api_key",
      data.openai_oauth_configured || false,
      data.openai_configured || false
    );

    if (data.llm_model_name && byId("llm-model")) {
      byId("llm-model").value = data.llm_model_name;
    }

    byId("storage-type").value = data.storage_type || "s3";
    byId("endpoint-url").value = data.s3_endpoint_url || "";
    byId("bucket-name").value = data.s3_bucket_name || "";
    toggleRemote();
    message("");
  } catch (error) {
    message(error.message, true);
  }
}

byId("oauth-login-btn").addEventListener("click", startOAuthFlow);
byId("oauth-unlink-btn").addEventListener("click", unlinkOAuth);
byId("storage-type").addEventListener("change", toggleRemote);

byId("settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = byId("save-button");
  button.disabled = true;
  message("저장 중…");

  const apiKeyVal = byId("openai-key").value || null;
  const payload = {
    llm_auth_type: currentAuthType,
    llm_model_name: byId("llm-model") ? byId("llm-model").value : "gpt-5.6-luna",
    openai_api_key: apiKeyVal,
    embedding_api_key: apiKeyVal,
    storage_type: byId("storage-type").value,
    s3_endpoint_url: byId("endpoint-url").value || null,
    s3_bucket_name: byId("bucket-name").value || null,
    s3_access_key_id: byId("access-key").value || null,
    s3_secret_access_key: byId("secret-key").value || null
  };
  try {
    const response = await fetch("/api/settings", { method: "PUT", headers: headers(), body: JSON.stringify(payload) });
    let data;
    try {
      data = await response.json();
    } catch (e) {
      throw new Error(`저장 실패 (HTTP ${response.status})`);
    }
    if (!response.ok) throw new Error(data.detail || "저장하지 못했습니다.");
    location.replace("/settings");
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
  }
});
loadSettings();
