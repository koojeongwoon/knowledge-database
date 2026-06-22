import os
import hashlib
import base64
from typing import List, Dict, Any
from openai import OpenAI

class ImageProcessor:
    def __init__(self, root_dir: str = "."):
        self.root_dir = os.path.abspath(root_dir)
        self.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")
        
        # OpenAI API Key 초기화
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.client = None
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            print("[!] Warning: OPENAI_API_KEY not found. Image processing will run in Mock mode.")

    def _get_image_hash(self, file_path: str) -> str:
        """이미지 파일의 MD5 해시를 구합니다."""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            buf = f.read(8192)
            while len(buf) > 0:
                hasher.update(buf)
                buf = f.read(8192)
        return hasher.hexdigest()

    def _encode_image_to_base64(self, file_path: str) -> str:
        """이미지 파일을 Base64 문자열로 인코딩합니다."""
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _parse_sidecar_hash(self, sidecar_path: str) -> str:
        """기존 사이드카 마크다운 파일에서 content_hash 값을 파싱합니다."""
        if not os.path.exists(sidecar_path):
            return ""
        
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Simple YAML frontmatter parsing
            import re
            match = re.search(r"content_hash:\s*['\"]?([a-fA-F0-9]+)['\"]?", content)
            if match:
                return match.group(1)
        except Exception as e:
            print(f"[-] Error reading sidecar file {sidecar_path}: {e}")
        
        return ""

    def _analyze_image_via_vlm(self, image_path: str) -> Dict[str, Any]:
        """OpenAI gpt-4o-mini Vision API를 사용하여 이미지를 분석합니다."""
        if not self.client:
            # API 키가 없는 경우 Mock 데이터 생성 (테스트용)
            print(f"[*] Running Mock Vision analysis for {image_path}...")
            filename = os.path.basename(image_path)
            return {
                "title": f"Image: {filename}",
                "description": f"Mock description for {filename}",
                "tags": ["mock", "image"],
                "analysis": "API Key가 설정되지 않아 Vision 분석 결과가 생성되지 않았습니다.",
                "ocr": ""
            }

        base64_image = self._encode_image_to_base64(image_path)
        filename = os.path.basename(image_path)
        
        # 파일 확장자에 따른 mime type 감지
        ext = os.path.splitext(filename)[1].lower()
        mime_type = "image/jpeg"
        if ext == ".png":
            mime_type = "image/png"
        elif ext == ".webp":
            mime_type = "image/webp"
        elif ext == ".gif":
            mime_type = "image/gif"

        prompt = """너는 지식베이스 RAG 시스템을 위한 고도의 이미지 분석가이다.
이 이미지를 다각도로 분석하여 사용자가 텍스트 기반 RAG 검색으로 이 이미지를 쉽게 찾아낼 수 있도록 정보를 추출해라.

다음의 5가지 요소를 한국어로 상세히 추출해라:
1. **Title**: 이 이미지의 핵심 특징을 5자 이내로 요약한 명확한 제목 (예: 'K8s 아키텍처 다이어그램', '모던 거실 인테리어')
2. **Description**: 이미지 전체의 구도, 목적, 핵심 맥락을 요약한 한 줄 설명
3. **Tags**: 이미지를 대표하는 키워드 태그 3~5개 (JSON list 형식으로 반환, 예: ["kubernetes", "architecture", "network"])
4. **Visual Analysis (오브젝트 & 스타일)**:
   - 이미지 내에 존재하는 주요 물리적/추상적 객체(오브젝트)들을 모두 나열하고 묘사해라. (인테리어 소품, 서버 노드, 아이콘 등)
   - 색감(컬러 스키마), 조명 상태, 전체적인 시각적 디자인 스타일(예: 북유럽풍, 모던, 우드톤, 와이어프레임)을 묘사해라.
5. **OCR (Text Extraction)**:
   - 이미지 내에 글자가 존재한다면, 누락 없이 모든 영문/국문 텍스트를 추출해서 구조화하여 작성해라. 에러 로그나 소스코드가 캡처되어 있다면 코드 블록으로 표현해라.

결과는 반드시 아래의 포맷으로만 답변해야 하며, 추가적인 인사말이나 마크다운 백틱(```) 등은 일체 포함하지 마라.

---
title: "[1번 제목]"
description: "[2번 요약 설명]"
tags: [3번 태그들 (JSON list 포맷)]
---

### 1. 시각 분석 (객체 및 스타일)
[4번 내용]

### 2. 텍스트 추출 (OCR)
[5번 내용]
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1500
            )
            raw_result = response.choices[0].message.content.strip()
            return raw_result
        except Exception as e:
            print(f"[✗] VLM API call failed for {image_path}: {e}")
            raise e

    def process_images(self) -> Dict[str, int]:
        """
        증분 이미지 처리 루프를 실행합니다.
        - 변경된 이미지만 VLM API 호출 후 사이드카 마크다운 파일로 작성합니다.
        """
        stats = {"processed": 0, "skipped": 0, "errors": 0}
        
        # 1. 대상 디렉토리 스캔
        image_files = []
        for target in self.target_dirs:
            target_path = os.path.join(self.root_dir, target)
            if not os.path.exists(target_path):
                continue
            
            for root, _, files in os.walk(target_path):
                for file in files:
                    if file.lower().endswith(self.image_extensions):
                        # 사이드카 마크다운 파일 자체는 수집 제외
                        image_files.append(os.path.join(root, file))
        
        if not image_files:
            return stats
            
        print(f"[*] Found {len(image_files)} image file(s) to scan...")

        for img_path in image_files:
            rel_img_path = os.path.relpath(img_path, self.root_dir)
            sidecar_path = img_path + ".md"
            
            try:
                # 2. 증분 검사 (MD5 Hash 비교)
                current_hash = self._get_image_hash(img_path)
                existing_hash = self._parse_sidecar_hash(sidecar_path)
                
                if current_hash == existing_hash and os.path.exists(sidecar_path):
                    stats["skipped"] += 1
                    continue

                print(f"[+] Processing new/modified image: {rel_img_path}...")
                
                # 3. Vision API 분석 실행
                vlm_result = self._analyze_image_via_vlm(img_path)
                
                # 4. 사이드카 마크다운 파일 작성
                import re
                
                # VLM 결과가 yaml 형식을 갖추고 있다면
                if vlm_result.startswith("---"):
                    parts = vlm_result.split("---", 2)
                    if len(parts) >= 3:
                        yaml_content = parts[1]
                        body_content = parts[2]
                        
                        # 중복 방지를 위해 기존 key 제거
                        yaml_content = re.sub(r"content_hash:.*?\n", "", yaml_content)
                        yaml_content = re.sub(r"image_path:.*?\n", "", yaml_content)
                        yaml_content = re.sub(r"type:.*?\n", "", yaml_content)
                        
                        updated_yaml = (
                            f"type: ImageSummary\n"
                            f"image_path: \"{rel_img_path}\"\n"
                            f"content_hash: \"{current_hash}\"\n"
                            f"{yaml_content.strip()}\n"
                        )
                        final_content = f"---\n{updated_yaml}---\n{body_content}"
                    else:
                        final_content = vlm_result
                else:
                    # YAML 형식이 안 지켜진 경우 전체 바디로 처리
                    final_content = (
                        f"---\n"
                        f"type: ImageSummary\n"
                        f"image_path: \"{rel_img_path}\"\n"
                        f"content_hash: \"{current_hash}\"\n"
                        f"title: \"Image: {os.path.basename(img_path)}\"\n"
                        f"description: \"Auto generated summary\"\n"
                        f"tags: [\"image\"]\n"
                        f"---\n\n"
                        f"{vlm_result}"
                    )
                
                with open(sidecar_path, "w", encoding="utf-8") as f:
                    f.write(final_content)
                
                stats["processed"] += 1
                
            except Exception as e:
                print(f"[✗] Failed to process image {rel_img_path}: {e}")
                stats["errors"] += 1
                
        # 5. 원본이 삭제된 고아 사이드카 마크다운 파일 청소
        self._cleanup_orphan_sidecars()
                
        return stats

    def _cleanup_orphan_sidecars(self):
        """원본 이미지 파일이 삭제되었는데 남아있는 고아 사이드카 마크다운 파일을 제거합니다."""
        import re
        for target in self.target_dirs:
            target_path = os.path.join(self.root_dir, target)
            if not os.path.exists(target_path):
                continue
            
            for root, _, files in os.walk(target_path):
                for file in files:
                    if file.lower().endswith(".md"):
                        full_path = os.path.join(root, file)
                        try:
                            with open(full_path, "r", encoding="utf-8") as f:
                                # Frontmatter 확인을 위해 앞부분만 읽음
                                content = f.read(1000)
                            
                            if "type: ImageSummary" in content:
                                match = re.search(r"image_path:\s*['\"]?(.*?)['\"]?\n", content)
                                if match:
                                    rel_img_path = match.group(1)
                                    abs_img_path = os.path.abspath(os.path.join(self.root_dir, rel_img_path))
                                    
                                    # 원본 이미지가 실제로 존재하지 않으면 사이드카 삭제
                                    if not os.path.exists(abs_img_path):
                                        rel_sidecar_path = os.path.relpath(full_path, self.root_dir)
                                        print(f"[-] Cleaning up orphan image sidecar (original image removed): {rel_sidecar_path}")
                                        os.remove(full_path)
                        except Exception as e:
                            # 파일 읽기 실패 시 스킵
                            pass
