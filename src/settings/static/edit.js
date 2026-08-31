const viewStyles=document.createElement("link");viewStyles.rel="stylesheet";viewStyles.href="/settings/assets/settings-view.css";document.head.append(viewStyles);
const byId=id=>document.getElementById(id),headers=()=>({"Content-Type":"application/json"});
function badge(id,text,good=false){const el=byId(id);el.textContent=text;el.className=`badge ${good?"good":"neutral"}`}
function toggleRemote(){badge("storage-state",byId("storage-type").selectedOptions[0].text,true)}
function toggleLlmAuth(){
  const type=byId("llm-auth-type").value;
  byId("api-key-group").style.display=type==="api_key"?"block":"none";
  byId("oauth-group").style.display=type==="openai_oauth"?"block":"none";
}
function message(text,error=false){const el=byId("message");el.textContent=text;el.className=`message${error?" error":""}`}

let pollTimer=null;
async function startOAuthFlow(){
  const btn=byId("oauth-login-btn"),guide=byId("oauth-device-guide"),statusText=byId("oauth-status-text");
  btn.disabled=true;
  statusText.textContent="인증 코드 요청 중…";
  try{
    const res=await fetch("/api/settings/openai-oauth/device-code",{method:"POST"});
    const data=await res.json();
    if(!res.ok)throw new Error(data.detail||"인증 코드를 받지 못했습니다.");
    
    byId("oauth-user-code").textContent=data.user_code;
    const uri=data.verification_uri_complete||data.verification_uri;
    byId("oauth-uri-link").href=uri;
    guide.style.display="block";
    statusText.textContent="브라우저에서 승인을 완료해 주세요.";
    
    window.open(uri,"_blank");
    
    if(pollTimer)clearInterval(pollTimer);
    const interval=(data.interval||5)*1000;
    pollTimer=setInterval(async()=>{
      try{
        const pollRes=await fetch("/api/settings/openai-oauth/poll",{
          method:"POST",
          headers:headers(),
          body:JSON.stringify({device_code:data.device_code})
        });
        const pollData=await pollRes.json();
        if(pollData.status==="complete"){
          clearInterval(pollTimer);
          statusText.textContent="✓ ChatGPT 계정 연동 완료!";
          badge("llm-state","ChatGPT OAuth 연결됨",true);
          guide.style.display="none";
          btn.disabled=false;
          message("ChatGPT OAuth 인증이 성공적으로 저장되었습니다!");
        }else if(pollData.status==="pending"){
          byId("oauth-poll-message").textContent="브라우저 승인 대기 중… ("+new Date().toLocaleTimeString()+")";
        }
      }catch(e){
        console.error(e);
      }
    },interval);
  }catch(e){
    statusText.textContent="오류: "+e.message;
    btn.disabled=false;
  }
}

async function loadSettings(){
  try{
    const response=await fetch("/api/settings");
    if(response.status===401){location.replace("/login");return}
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail||"설정을 불러오지 못했습니다.");
    
    byId("llm-auth-type").value=data.llm_auth_type||"api_key";
    toggleLlmAuth();
    
    if(data.llm_auth_type==="openai_oauth"&&data.openai_oauth_configured){
      badge("llm-state","ChatGPT OAuth 연결됨",true);
      byId("oauth-status-text").textContent="연결된 계정 세션 활성";
    }else if(data.openai_configured){
      badge("llm-state","API Key 설정됨",true);
    }else{
      badge("llm-state","미설정",false);
    }
    
    byId("storage-type").value=data.storage_type||"s3";
    byId("endpoint-url").value=data.s3_endpoint_url||"";
    byId("bucket-name").value=data.s3_bucket_name||"";
    toggleRemote();
    message("");
  }catch(error){
    message(error.message,true);
  }
}

byId("llm-auth-type").addEventListener("change",toggleLlmAuth);
byId("oauth-login-btn").addEventListener("click",startOAuthFlow);
byId("storage-type").addEventListener("change",toggleRemote);

byId("settings-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const button=byId("save-button");
  button.disabled=true;
  message("저장 중…");
  const payload={
    llm_auth_type:byId("llm-auth-type").value,
    openai_api_key:byId("openai-key").value||null,
    embedding_api_key:byId("embedding-key").value||null,
    storage_type:byId("storage-type").value,
    s3_endpoint_url:byId("endpoint-url").value||null,
    s3_bucket_name:byId("bucket-name").value||null,
    s3_access_key_id:byId("access-key").value||null,
    s3_secret_access_key:byId("secret-key").value||null
  };
  try{
    const response=await fetch("/api/settings",{method:"PUT",headers:headers(),body:JSON.stringify(payload)});
    const data=await response.json();
    if(!response.ok)throw new Error(data.detail||"저장하지 못했습니다.");
    location.replace("/settings");
  }catch(error){
    message(error.message,true);
  }finally{
    button.disabled=false;
  }
});
loadSettings();

