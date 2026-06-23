import os
import hashlib
from typing import Dict, Any, List

class MediaFile:
    """원본 미디어 자원 파일(이미지, 오디오 등)을 표현하는 도메인 엔티티"""
    def __init__(self, full_path: str, root_dir: str):
        self.full_path = os.path.abspath(full_path)
        self.filename = os.path.basename(full_path)
        self.rel_path = os.path.relpath(self.full_path, root_dir)
        self._content_hash = None

    @property
    def content_hash(self) -> str:
        """파일의 MD5 해시 값을 지연 로딩하여 계산합니다. (도메인 비즈니스 룰)"""
        if self._content_hash is None:
            hasher = hashlib.md5()
            with open(self.full_path, "rb") as f:
                buf = f.read(8192)
                while len(buf) > 0:
                    hasher.update(buf)
                    buf = f.read(8192)
            self._content_hash = hasher.hexdigest()
        return self._content_hash

    def is_modified(self, sidecar_hash: str) -> bool:
        """기존 사이드카 캐시의 해시값과 비교하여 변경 여부를 판별합니다."""
        return self.content_hash != sidecar_hash


class MediaSummary:
    """미디어 자료에서 AI가 분석한 원본 정보 표현 객체 (Value Object)"""
    def __init__(self, title: str, description: str, tags: List[str], visual_analysis: str = "", ocr_text: str = ""):
        self.title = title
        self.description = description
        self.tags = tags
        self.visual_analysis = visual_analysis
        self.ocr_text = ocr_text


class SidecarDocument:
    """원본 미디어와 매핑되어 생성되는 사이드카 마크다운 문서 엔티티"""
    def __init__(self, media_file: MediaFile, doc_type: str):
        self.media_file = media_file
        self.doc_type = doc_type  # ImageSummary, AudioSummary 등
        self.sidecar_path = media_file.full_path + ".md"

    def build_markdown_content(self, summary: MediaSummary) -> str:
        """통일된 YAML Frontmatter를 갖춘 마크다운 문서를 구성합니다. (도메인 포맷 위임)"""
        import json
        tags_formatted = json.dumps(summary.tags, ensure_ascii=False)
        
        # Frontmatter 템플릿 컴파일
        content = f"""---
type: {self.doc_type}
image_path: "{self.media_file.rel_path}"
source_path: "assets/{self.media_file.filename}"
content_hash: "{self.media_file.content_hash}"
title: "{summary.title}"
description: "{summary.description}"
tags: {tags_formatted}
---

### 1. 시각 분석 및 상세 묘사
{summary.visual_analysis}

### 2. 텍스트 추출 및 오디오 타임라인 (OCR/STT)
{summary.ocr_text}
"""
        return content
