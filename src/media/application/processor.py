import os
from typing import Dict


class ImageProcessor:
    def __init__(self, root_dir: str = "."):
        self.root_dir = os.path.abspath(root_dir)
        self.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    def _parse_sidecar_hash(self, sidecar_path: str) -> str:
        """기존 사이드카 마크다운 파일에서 content_hash 값을 파싱합니다."""
        if not os.path.exists(sidecar_path):
            return ""
        
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            import re
            match = re.search(r"content_hash:\s*['\"]?([a-fA-F0-9]+)['\"]?", content)
            if match:
                return match.group(1)
        except Exception as e:
            print(f"[-] Error reading sidecar file {sidecar_path}: {e}")
        
        return ""

    def process_images(self) -> Dict[str, int]:
        """
        증분 이미지 처리 루프를 실행합니다.
        외부 VLM API를 호출하지 않고, 에이전트 클라이언트가 원본 이미지 바이트를 보고 
        직접 시각 분석을 수행하도록 경로를 바인딩해주는 사이드카 인덱스(.png.md)만 신속하게 빌드합니다.
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
            
        print(f"[*] Scanning {len(image_files)} image file(s) for index metadata...")

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

                print(f"[+] Indexing new/modified image asset: {media_file.rel_path}")
                
                # 3. 외부 API 호출을 배제하고 에이전트 자체 해석용 바인딩 데이터 생성
                title = f"Image: {media_file.filename}"
                description = f"에이전트 클라이언트가 자체 멀티모달 Vision으로 직접 분석하는 이미지 자산입니다. ({media_file.filename})"
                tags = ["image"]
                visual_analysis = f"이 이미지는 에이전트(클라이언트)가 직접 원본 파일({media_file.rel_path})을 인계받아 실시간 분석을 처리합니다."
                
                # 4. 도메인 값 객체(MediaSummary)로 데이터 캡슐화
                summary = MediaSummary(
                    title=title,
                    description=description,
                    tags=tags,
                    visual_analysis=visual_analysis,
                    ocr_text=""
                )
                
                # 5. 사이드카 마크다운 본문 저장
                final_content = sidecar_doc.build_markdown_content(summary)
                
                with open(sidecar_doc.sidecar_path, "w", encoding="utf-8") as f:
                    f.write(final_content)
                
                stats["processed"] += 1
                
            except Exception as e:
                print(f"[✗] Failed to index image {os.path.basename(img_path)}: {e}")
                stats["errors"] += 1
                
        # 6. 고아 사이드카 마크다운 파일 청소
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
                                content = f.read(1000)
                            
                            if "type: ImageSummary" in content:
                                match = re.search(r"image_path:\s*['\"]?(.*?)['\"]?\n", content)
                                if match:
                                    rel_img_path = match.group(1)
                                    abs_img_path = os.path.abspath(os.path.join(self.root_dir, rel_img_path))
                                    
                                    if not os.path.exists(abs_img_path):
                                        rel_sidecar_path = os.path.relpath(full_path, self.root_dir)
                                        print(f"[-] Cleaning up orphan image sidecar (original image removed): {rel_sidecar_path}")
                                        os.remove(full_path)
                        except Exception:
                            pass
