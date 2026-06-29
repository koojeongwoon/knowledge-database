import os
import json
import glob
import re
import shutil
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
from src.indexing.domain.refresher import RefreshTarget

class KnowledgeRefresher:
    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.schedules_dir = os.path.join(self.root_dir, ".agents", "schedules")
        self.topic_map_path = os.path.join(self.root_dir, ".agents", "topic_map.json")
        os.makedirs(self.schedules_dir, exist_ok=True)
        
        # topic_map.json이 없으면 기본 초기화
        if not os.path.exists(self.topic_map_path):
            with open(self.topic_map_path, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=2)

    def _load_topic_map(self) -> Dict[str, List[str]]:
        try:
            with open(self.topic_map_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_topic_map(self, topic_map: Dict[str, List[str]]):
        try:
            with open(self.topic_map_path, 'w', encoding='utf-8') as f:
                json.dump(topic_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save topic map: {e}")

    def _find_category_for_topic(self, rel_path: str) -> str:
        """topic_map.json을 조회하여 해당 노트가 속한 대범주 카테고리를 리턴합니다."""
        topic_map = self._load_topic_map()
        filename = os.path.basename(rel_path)
        for category, files in topic_map.items():
            for f in files:
                if os.path.basename(f) == filename:
                    return category
        return "Uncategorized"

    def get_expired_targets(self) -> List[Dict[str, Any]]:
        """
        topic_map.json 및 schedules/ 내의 범주형 JSON 파일을 스캔하여
        갱신 주기가 도달한 노트들의 목록을 수집합니다.
        """
        expired_targets = []
        current_time = datetime.now(timezone.utc)
        topic_map = self._load_topic_map()

        for category in topic_map.keys():
            schedule_path = os.path.join(self.schedules_dir, f"{category}.json")
            if not os.path.exists(schedule_path):
                continue

            try:
                with open(schedule_path, 'r', encoding='utf-8') as f:
                    schedule_data = json.load(f)
            except Exception:
                continue

            for rel_path, info in schedule_data.items():
                target = RefreshTarget(
                    file_path=rel_path,
                    refresh_interval=info.get("refresh_interval", "never"),
                    refresh_source=info.get("refresh_source", "web_search"),
                    last_refresh=info.get("last_refresh")
                )

                if target.is_expired(current_time):
                    expired_targets.append({
                        "file_path": rel_path,
                        "refresh_source": target.refresh_source,
                        "refresh_interval": target.refresh_interval,
                        "last_refresh": target.last_refresh,
                        "category": category,
                        "query": os.path.splitext(os.path.basename(rel_path))[0]
                    })

        return expired_targets

    def update_last_refresh(self, rel_path: str):
        """특정 노트의 갱신 검사 완료 일자를 오늘 날짜로 업데이트합니다."""
        category = self._find_category_for_topic(rel_path)
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        schedule_path = os.path.join(self.schedules_dir, f"{category}.json")
        if not os.path.exists(schedule_path):
            return

        try:
            with open(schedule_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            filename = os.path.basename(rel_path)
            for k in list(data.keys()):
                if os.path.basename(k) == filename:
                    data[k]["last_refresh"] = current_date
            
            with open(schedule_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to update last_refresh for {rel_path} in {category}.json: {e}")

    def update_or_create_schedule(self, rel_path: str, interval: str, source: str, category: str = "Development"):
        """대화를 통해 특정 노트의 스케줄을 갱신하고 토픽 맵에 등록하며, 물리 파일도 카테고리 폴더 하위로 자동 이동시킵니다."""
        # 1. 물리적 파일 이동 처리
        filename = os.path.basename(rel_path)
        old_full_path = os.path.join(self.root_dir, rel_path)
        
        new_rel_path = f"topics/{category}/{filename}"
        new_full_path = os.path.join(self.root_dir, new_rel_path)
        
        if os.path.exists(old_full_path) and old_full_path != new_full_path:
            os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
            try:
                shutil.move(old_full_path, new_full_path)
                print(f"[+] Physical file moved: {rel_path} -> {new_rel_path}")
                rel_path = new_rel_path
            except Exception as e:
                print(f"[-] Failed to move file to category folder: {e}")
        else:
            if os.path.exists(new_full_path):
                rel_path = new_rel_path

        topic_map = self._load_topic_map()
        
        # 2. 토픽 맵에서 중복 경로 제거 및 정리
        for cat, files in list(topic_map.items()):
            topic_map[cat] = [f for f in files if os.path.basename(f) != filename]
            if not topic_map[cat]:
                del topic_map[cat]
                old_schedule = os.path.join(self.schedules_dir, f"{cat}.json")
                if os.path.exists(old_schedule):
                    try:
                        os.remove(old_schedule)
                    except Exception:
                        pass

        # 3. 지정된 새 카테고리에 신규 경로 등록 및 토픽 맵 저장
        if category not in topic_map:
            topic_map[category] = []
        if rel_path not in topic_map[category]:
            topic_map[category].append(rel_path)
        
        self._save_topic_map(topic_map)

        # 4. 해당하는 범주형 스케줄 파일 업데이트
        schedule_path = os.path.join(self.schedules_dir, f"{category}.json")
        schedule_data = {}
        if os.path.exists(schedule_path):
            try:
                with open(schedule_path, 'r', encoding='utf-8') as f:
                    schedule_data = json.load(f)
            except Exception:
                pass

        for k in list(schedule_data.keys()):
            if os.path.basename(k) == filename:
                del schedule_data[k]

        schedule_data[rel_path] = {
            "refresh_interval": interval,
            "refresh_source": source,
            "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%d")
        }

        with open(schedule_path, 'w', encoding='utf-8') as f:
            json.dump(schedule_data, f, ensure_ascii=False, indent=2)

    def evaluate_drift(self, rel_path: str, latest_text: str) -> Dict[str, Any]:
        """로컬 파일과 최신 수집본을 대조하여 지식 괴리(Drift)를 판독합니다."""
        full_path = os.path.join(self.root_dir, rel_path)
        if not os.path.exists(full_path):
            category = self._find_category_for_topic(rel_path)
            filename = os.path.basename(rel_path)
            moved_path = os.path.join(self.root_dir, "topics", category, filename)
            if os.path.exists(moved_path):
                full_path = moved_path
                rel_path = f"topics/{category}/{filename}"
            else:
                return {"status": "ERROR", "reason": f"File not found: {rel_path}"}

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                local_content = f.read()
        except Exception as e:
            return {"status": "ERROR", "reason": f"Failed to read local file: {e}"}

        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"status": "ERROR", "reason": "OPENAI_API_KEY environment variable is not set."}
        
        client = OpenAI(api_key=api_key)

        prompt = f"""You are a Knowledge Integrity Auditor. 
Compare the [Local Knowledge Document] with the [Latest Collected Data].
Determine if there are any contradictions, factual drifts, outdated parameters, or critical omissions in the Local Document compared to the Latest Data.

[Local Knowledge Document]
---
{local_content}
---

[Latest Collected Data]
---
{latest_text}
---

Output format requirements:
If there is a drift (factual update/correction needed), start your response with 'STATUS: DRIFT_DETECTED' followed by a bulleted explanation of what drifted and a suggested merge markdown text block under 'SUGGESTED_MERGE'.
If everything is accurate and up-to-date, respond with 'STATUS: NO_DRIFT'.
"""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a precise fact-checking assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            result_text = response.choices[0].message.content.strip()
        except Exception as e:
            return {"status": "ERROR", "reason": f"API call failed: {e}"}

        if "DRIFT_DETECTED" in result_text:
            scratch_dir = os.path.join(self.root_dir, "scratch")
            os.makedirs(scratch_dir, exist_ok=True)
            
            filename = os.path.basename(rel_path)
            draft_path = os.path.join(scratch_dir, f"draft_{filename}")
            
            try:
                with open(draft_path, 'w', encoding='utf-8') as f:
                    f.write(result_text)
            except Exception:
                pass

            return {
                "status": "DRIFT_DETECTED",
                "analysis": result_text,
                "draft_path": os.path.relpath(draft_path, self.root_dir)
            }
        
        return {"status": "NO_DRIFT"}

    def apply_merge(self, rel_path: str) -> str:
        """초안 갱신본을 병합하고 갱신 타임스탬프를 갱신하며, 기존 구버전은 범주별 archive 폴더에 백업 격리합니다."""
        filename = os.path.basename(rel_path)
        scratch_dir = os.path.join(self.root_dir, "scratch")
        draft_path = os.path.join(scratch_dir, f"draft_{filename}")
        full_path = os.path.join(self.root_dir, rel_path)

        if not os.path.exists(full_path):
            category = self._find_category_for_topic(rel_path)
            moved_path = os.path.join(self.root_dir, "topics", category, filename)
            if os.path.exists(moved_path):
                full_path = moved_path
                rel_path = f"topics/{category}/{filename}"

        if not os.path.exists(draft_path):
            return f"실패: 임시 갱신 초안을 찾을 수 없습니다. ({draft_path})"
        
        if not os.path.exists(full_path):
            return f"실패: 원본 파일을 찾을 수 없습니다. ({rel_path})"

        try:
            with open(draft_path, 'r', encoding='utf-8') as f:
                draft_content = f.read()
        except Exception as e:
            return f"실패: 갱신 초안 읽기 실패: {e}"

        suggested_text = ""
        if "SUGGESTED_MERGE" in draft_content:
            parts = draft_content.split("SUGGESTED_MERGE")
            if len(parts) > 1:
                suggested_text = parts[1].strip()
                if suggested_text.startswith("```"):
                    lines = suggested_text.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    suggested_text = "\n".join(lines).strip()

        if not suggested_text:
            suggested_text = draft_content

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                old_content = f.read()

            # 각 카테고리 내부의 archive 폴더로 이격 배정 (e.g., topics/Development/archive/)
            parent_dir = os.path.dirname(full_path)
            archive_dir = os.path.join(parent_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)

            now = datetime.now(timezone.utc)
            timestamp_str = now.strftime("%Y%m%d_%H%M")
            base_name = os.path.splitext(filename)[0]
            
            archive_filepath = os.path.join(archive_dir, f"{base_name}_{timestamp_str}.md")

            archive_meta = f"""---
type: Archive
archived_from: "{rel_path}"
archived_at: "{now.isoformat()}"
title: "[Archive] {base_name} ({timestamp_str})"
tags: ["archive"]
---

"""
            cleaned_old_body = old_content
            if old_content.strip().startswith("---"):
                parts = old_content.split("---", 2)
                if len(parts) >= 3:
                    cleaned_old_body = parts[2].strip()

            with open(archive_filepath, 'w', encoding='utf-8') as f:
                f.write(archive_meta + cleaned_old_body)

            frontmatter_header = ""
            if old_content.strip().startswith("---"):
                parts = old_content.split("---", 2)
                if len(parts) >= 3:
                    fm_text = parts[1]
                    fm_text = re.sub(
                        r'timestamp:.*', 
                        f'timestamp: "{now.isoformat()}"', 
                        fm_text
                    )
                    frontmatter_header = f"---{fm_text}---\n\n"

            if not frontmatter_header:
                frontmatter_header = f"""---
type: TopicSummary
title: "{base_name}"
timestamp: "{now.isoformat()}"
---

"""
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(frontmatter_header + suggested_text)

            os.remove(draft_path)
            self.update_last_refresh(rel_path)
            
            # 리배치된 새로운 아카이브 경로 리턴
            rel_archive_path = os.path.relpath(archive_filepath, self.root_dir)
            return f"성공: {rel_path} 지식 갱신 및 기존 본문 아카이빙 완료 (아카이브 경로: {rel_archive_path})"
        except Exception as e:
            return f"실패: 지식 아카이빙 및 병합 중 예외 발생: {e}"
