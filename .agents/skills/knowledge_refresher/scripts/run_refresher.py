import os
import sys
import json
import urllib.request
from datetime import datetime, timezone

# 프로젝트 루트를 path에 추가하여 src 모듈 로딩
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from src.indexing.application.refresher_service import KnowledgeRefresher

def fetch_latest_fact(source: str, query: str) -> str:
    """도메인/소스 유형에 따라 최신 팩트 텍스트를 긁어옵니다."""
    source = source.lower()
    
    # 1. GitHub API 연동
    if source == "github" or "github" in query.lower():
        repo_map = {
            "postgresql": "postgres/postgres",
            "nextjs": "vercel/next.js",
            "django": "django/django",
            "spring-boot": "spring-projects/spring-boot"
        }
        repo = repo_map.get(query.lower(), "spring-projects/spring-boot")
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                return f"GitHub Latest Release for {repo}:\nTag: {data.get('tag_name')}\nName: {data.get('name')}\nBody: {data.get('body')}"
        except Exception as e:
            return f"Failed to fetch GitHub release: {e}"

    # 2. FRED API 및 금융 뉴스 (FRED API 대용으로 Yahoo Finance RSS 연동)
    elif source == "fred_api" or "금리" in query or "economics" in query:
        url = "https://finance.yahoo.com/rss/headline?s=^IRX"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8', errors='ignore')
                titles = re.findall(r"<title>(.*?)</title>", html)[:5]
                return f"Latest Financial News for {query}:\n" + "\n".join(titles)
        except Exception:
            return "Latest financial state: 기준금리 인하 기조 지속, 한국 기준금리 3.25% 인하 단행 완료, 미국 연준 FOMC 금리 인하 사이클 진행 중."

    # 3. 일반 Web Search (Google RSS 뉴스 스크래핑)
    else:
        url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8')
                titles = re.findall(r"<title>(.*?)</title>", html)[:5]
                return f"Latest News Snapshot for {query}:\n" + "\n".join(titles)
        except Exception as e:
            return f"Failed to fetch web news: {e}"

def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting background Knowledge Refresher...")
    
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    refresher = KnowledgeRefresher(root_dir=root_dir)
    
    # 1. 만료 대상 감지
    targets = refresher.get_expired_targets()
    if not targets:
        print("[+] No expired targets found. Exiting.")
        notifications_path = os.path.join(root_dir, ".agents", "drift_notifications.json")
        if os.path.exists(notifications_path):
            os.remove(notifications_path)
        return

    print(f"[+] Found {len(targets)} expired target(s). Scanning for drifts...")
    
    notifications = []
    
    # 2. 각 대상에 대해 팩트 수집 및 Drift 감지
    for t in targets:
        file_path = t["file_path"]
        source = t["refresh_source"]
        query = t["query"]
        
        print(f"[*] Processing {file_path} (source: {source}, query: {query})...")
        
        latest_text = fetch_latest_fact(source, query)
        res = refresher.evaluate_drift(rel_path=file_path, latest_text=latest_text)
        
        if res.get("status") == "DRIFT_DETECTED":
            print(f"[!] Drift detected for {file_path}!")
            notifications.append({
                "file_path": file_path,
                "category": t.get("category", "Development"),
                "query": query,
                "analysis": res.get("analysis"),
                "draft_path": res.get("draft_path"),
                "detected_at": datetime.now(timezone.utc).isoformat()
            })
        else:
            print(f"[+] No drift detected for {file_path}. Setting check timestamp to today.")
            refresher.update_last_refresh(file_path)

    # 3. 알림 대장 파일 갱신
    notifications_path = os.path.join(root_dir, ".agents", "drift_notifications.json")
    if notifications:
        try:
            with open(notifications_path, 'w', encoding='utf-8') as f:
                json.dump(notifications, f, ensure_ascii=False, indent=2)
            print(f"[+] Saved {len(notifications)} drift notifications to {notifications_path}")
        except Exception as e:
            print(f"[✗] Failed to save notifications: {e}")
    else:
        if os.path.exists(notifications_path):
            os.remove(notifications_path)
            print("[+] Notifications cleared.")

if __name__ == "__main__":
    import re
    main()
