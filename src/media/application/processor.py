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

        from src.media.domain.prompts import IMAGE_ANALYSIS_PROMPT
        prompt = IMAGE_ANALYSIS_PROMPT

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
        - 비즈니스 정책(파일 해시, 마크다운 조립)은 Media 도메인 모델에 위임합니다.
        """
        from src.media.domain.model import MediaFile, MediaSummary, SidecarDocument
        
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
                        image_files.append(os.path.join(root, file))
        
        if not image_files:
            return stats
            
        print(f"[*] Found {len(image_files)} image file(s) to scan...")

        for img_path in image_files:
            try:
                # 2. 도메인 엔티티(MediaFile)로 변환
                media_file = MediaFile(img_path, self.root_dir)
                sidecar_doc = SidecarDocument(media_file, "ImageSummary")
                
                # 증분 검사 (MD5 Hash 비교)
                existing_hash = self._parse_sidecar_hash(sidecar_doc.sidecar_path)
                
                if not media_file.is_modified(existing_hash) and os.path.exists(sidecar_doc.sidecar_path):
                    stats["skipped"] += 1
                    continue

                print(f"[+] Processing new/modified image: {media_file.rel_path}...")
                
                # 3. Vision API 분석 실행 (외부 VLM API 조율)
                vlm_result = self._analyze_image_via_vlm(img_path)
                
                # 4. 분석 결과 요약 데이터 정제 및 파싱
                import yaml
                title = f"Image: {media_file.filename}"
                description = "Auto generated summary"
                tags = ["image"]
                visual_analysis = vlm_result
                ocr_text = ""
                
                if vlm_result.startswith("---"):
                    parts = vlm_result.split("---", 2)
                    if len(parts) >= 3:
                        try:
                            fm = yaml.safe_load(parts[1])
                            title = fm.get("title", title)
                            description = fm.get("description", description)
                            tags = fm.get("tags", tags)
                        except Exception:
                            pass
                        body_content = parts[2].strip()
                        
                        body_parts = body_content.split("### 2. 텍스트 추출 (OCR)")
                        visual_analysis = body_parts[0].replace("### 1. 시각 분석 (객체 및 스타일)", "").strip()
                        if len(body_parts) > 1:
                            ocr_text = body_parts[1].strip()
                
                # 5. 도메인 값 객체(MediaSummary)로 데이터 캡슐화
                summary = MediaSummary(
                    title=title,
                    description=description,
                    tags=tags,
                    visual_analysis=visual_analysis,
                    ocr_text=ocr_text
                )
                
                # 6. 사이드카 마크다운 본문 작성을 도메인에 위임 후 저장
                final_content = sidecar_doc.build_markdown_content(summary)
                
                with open(sidecar_doc.sidecar_path, "w", encoding="utf-8") as f:
                    f.write(final_content)
                
                stats["processed"] += 1
                
            except Exception as e:
                print(f"[✗] Failed to process image {os.path.basename(img_path)}: {e}")
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
