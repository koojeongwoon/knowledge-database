import re
import datetime
from typing import List, Dict, Any

def slugify(text: str) -> str:
    """텍스트를 URL/파일명 친화적인 슬러그 형태로 변환합니다."""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    return re.sub(r'[-\s]+', '-', text).strip('-')

def build_journal_markdown(
    title: str, 
    description: str, 
    tags: List[str], 
    content: str, 
    timestamp: datetime.datetime
) -> str:
    """QAJournal 마크다운 파일의 전체 콘텐츠 구조를 조립합니다."""
    import json
    tags_formatted = json.dumps(tags, ensure_ascii=False)
    return f"""---
type: QAJournal
title: "{title}"
description: "{description}"
tags: {tags_formatted}
timestamp: "{timestamp.isoformat()}"
source: "agent-commit"
---

# {title}

{content}
"""

def build_sidecar_markdown(
    filename: str, 
    summary: Dict[str, Any], 
    timestamp: datetime.datetime
) -> str:
    """첨부파일의 메타데이터를 갖는 사이드카 마크다운 구조를 생성합니다."""
    import json
    s_type = summary.get("type", "DocumentSummary")
    s_title = summary.get("title", f"Summary: {filename}")
    s_desc = summary.get("description", "")
    s_tags = summary.get("tags", [])
    s_content = summary.get("content", "")
    
    s_tags_formatted = json.dumps(s_tags, ensure_ascii=False)
    
    return f"""---
type: {s_type}
source_path: "assets/{filename}"
title: "{s_title}"
description: "{s_desc}"
tags: {s_tags_formatted}
timestamp: "{timestamp.isoformat()}"
---

{s_content}
"""

def synthesize_topic(old_content: str, update_text: str, timestamp: datetime.datetime) -> str:
    """기존 TopicSummary 본문에 새로운 지식 갱신 내역을 합성하고 헤더 타임스탬프를 갱신합니다."""
    synthesis_text = f"\n\n### 업데이트 ({timestamp.strftime('%Y-%m-%d')})\n{update_text}"
    new_content = old_content + synthesis_text
    
    # YAML frontmatter의 timestamp 부분 갱신
    return re.sub(
        r'timestamp:.*', 
        f'timestamp: "{timestamp.isoformat()}"', 
        new_content
    )

def build_new_topic_markdown(
    topic_name: str, 
    tags: List[str], 
    update_text: str, 
    timestamp: datetime.datetime
) -> str:
    """신규 TopicSummary 마크다운 구조를 조립합니다."""
    import json
    tags_formatted = json.dumps(tags, ensure_ascii=False)
    return f"""---
type: TopicSummary
title: "{topic_name}"
description: "자동 생성된 토픽 정리본: {topic_name}"
tags: {tags_formatted}
timestamp: "{timestamp.isoformat()}"
---

# {topic_name}

{update_text or '내용을 입력하세요.'}
"""
