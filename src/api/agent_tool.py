from typing import List, Dict, Any
import json
from src.core.database.factory import DatabaseManager
from src.indexing.domain.embedding import FakeEmbeddingService, OpenAIEmbeddingService, BGEM3EmbeddingService
from src.core.config import EMBEDDING_PROVIDER, EMBEDDING_DIM, WIKI_DIR
from src.retrieval.application.service import WikiSearcher

def retrieve_wiki_knowledge(query: str, limit: int = 5) -> str:
    """
    AI 에이전트가 호출할 수 있는 도구(Tool) 함수입니다.
    사용자의 자연어 질문을 받아 로컬 K8s PostgreSQL pgvector 데이터베이스에서
    가장 관련성이 높은 마크다운 문서 조각들을 조회(Retrieval)하여 텍스트 형태로 리턴합니다.
    """
    # 1. 임베딩 공급자 선택
    if EMBEDDING_PROVIDER == "openai":
        embedding_service = OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
    elif EMBEDDING_PROVIDER == "bge-m3":
        embedding_service = BGEM3EmbeddingService()
    else:
        embedding_service = FakeEmbeddingService(dimension=EMBEDDING_DIM)
        
    db_manager = DatabaseManager()
    searcher = WikiSearcher(db_manager=db_manager, embedding_service=embedding_service)
    
    try:
        results = searcher.search(query, limit=limit)
        if not results:
            return "지식베이스에서 관련된 문서를 찾지 못했습니다."
            
        formatted_docs = []
        for doc in results:
            # Frontmatter에서 image_path 정보 추출 (이미지 RAG 연동용)
            raw_fm = doc.get("raw_frontmatter") or {}
            image_path = raw_fm.get("image_path")
            image_path_str = f"Image Path: {image_path}\n" if image_path else ""

            # 에이전트가 출처와 메타데이터를 인식하기 쉽도록 XML/Markdown 결합 포맷팅
            doc_str = (
                f"<document>\n"
                f"File: {doc['file_path']}\n"
                f"Title: {doc['title']}\n"
                f"Type: {doc['doc_type']}\n"
                f"{image_path_str}"
                f"Similarity Score: {doc['similarity']:.4f}\n"
                f"Tags: {', '.join(doc['tags']) if doc['tags'] else 'None'}\n"
                f"Content:\n{doc['content']}\n"
                f"</document>"
            )
            formatted_docs.append(doc_str)
            
        return "\n\n---\n\n".join(formatted_docs)
        
    except Exception as e:
        return f"지식베이스 조회 중 에러 발생: {str(e)}"
    finally:
        db_manager.close()

# 에이전트 프레임워크(예: LangChain) 연동을 위한 툴 정의 스키마 예시
# langchain_tool_spec = {
#     "name": "retrieve_wiki_knowledge",
#     "description": "개인 지식베이스(옵시디언 위키)에서 과거 Q&A 및 토픽을 조회하여 지식을 참조합니다.",
#     "func": retrieve_wiki_knowledge
# }

def commit_wiki_knowledge(title: str, description: str, tags: List[str], content: str, topic_name: str = None, topic_update_text: str = None, image_paths: List[str] = None, resource_paths: List[str] = None, resource_summaries: List[Dict[str, Any]] = None) -> str:
    """
    새로운 지식을 qa/ 저널에 기록하고, 선택적으로 topics/ 문서를 누적 업데이트합니다.
    대화 중 전달받은 미디어/자원 파일이 존재할 경우, assets/ 폴더로 복사하고 마크다운 본문에 링크를 삽입하며,
    resource_summaries가 있으면 통일된 Frontmatter를 가진 독립 요약 문서를 생성합니다.
    """
    import os
    import datetime
    import re
    import shutil

    root_dir = WIKI_DIR
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # 1. 파일명 슬러그 생성 헬퍼
    def slugify(text):
        text = text.lower()
        # 한글 및 영문, 숫자 허용
        text = re.sub(r'[^\w\s-]', '', text)
        return re.sub(r'[-\s]+', '-', text).strip('-')

    # 2. Q&A 저널 저장
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")
    title_slug = slugify(title)
    if not title_slug:
        title_slug = "qa-journal"
        
    # Page Bundle 구조: 글 하나당 독립된 폴더를 생성하여 관리
    qa_bundle_dir = os.path.join(root_dir, "qa", date_str, f"{time_str}-{title_slug}")
    os.makedirs(qa_bundle_dir, exist_ok=True)
    qa_file_path = os.path.join(qa_bundle_dir, f"{time_str}-{title_slug}.md")
    
    # 2-1. 리소스 파일(이미지, 오디오, 문서 등) 복사 및 링크 생성
    resource_info = ""
    # 호환성을 위해 image_paths와 resource_paths 통합
    all_resources = []
    if image_paths:
        all_resources.extend(image_paths)
    if resource_paths:
        all_resources.extend(resource_paths)
        
    # resource_summaries의 file_path들 통합
    summary_map = {}
    if resource_summaries:
        for summary in resource_summaries:
            f_path = summary.get("file_path")
            if f_path:
                all_resources.append(f_path)
                summary_map[f_path] = summary
                
    # 중복 제거
    all_resources = list(dict.fromkeys(all_resources))
    
    if all_resources:
        assets_dir = os.path.join(qa_bundle_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        
        copied_images = []
        copied_files = []
        image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")
        
        for res_path in all_resources:
            if os.path.exists(res_path):
                filename = os.path.basename(res_path)
                dest_path = os.path.join(assets_dir, filename)
                shutil.copy(res_path, dest_path)
                
                # 확장자에 맞춰 위키링크 생성
                is_image = filename.lower().endswith(image_extensions)
                if is_image:
                    copied_images.append(f"![[assets/{filename}]]")
                else:
                    copied_files.append(f"[[assets/{filename}]]")
                
                # 매핑된 요약 정보(summary)가 있다면 사이드카 .md 파일 생성
                if res_path in summary_map:
                    summary = summary_map[res_path]
                    s_type = summary.get("type", "DocumentSummary")
                    s_title = summary.get("title", f"Summary: {filename}")
                    s_desc = summary.get("description", "")
                    s_tags = summary.get("tags", [])
                    s_content = summary.get("content", "")
                    
                    s_tags_formatted = json.dumps(s_tags, ensure_ascii=False)
                    
                    sidecar_content = f"""---
type: {s_type}
source_path: "assets/{filename}"
title: "{s_title}"
description: "{s_desc}"
tags: {s_tags_formatted}
timestamp: "{now.isoformat()}"
---

{s_content}
"""
                    sidecar_file_path = os.path.join(assets_dir, f"{filename}.md")
                    with open(sidecar_file_path, "w", encoding="utf-8") as sf:
                        sf.write(sidecar_content)
        
        attachments_md = []
        if copied_images:
            attachments_md.append("### 첨부 이미지\n" + "\n".join(copied_images))
        if copied_files:
            attachments_md.append("### 첨부 파일 및 리소스\n" + "\n".join(copied_files))
            
        if attachments_md:
            content = content + "\n\n" + "\n\n".join(attachments_md)
            resource_info = f" (자원 {len(copied_images) + len(copied_files)}개 assets 복사 및 사이드카 {len(summary_map)}개 작성 완료)"
    
    # JSON 직렬화 시 한글 깨짐 방지
    tags_formatted = json.dumps(tags, ensure_ascii=False)
    
    qa_content = f"""---
type: QAJournal
title: "{title}"
description: "{description}"
tags: {tags_formatted}
timestamp: "{now.isoformat()}"
source: "agent-commit"
---

# {title}

{content}
"""
    try:
        with open(qa_file_path, 'w', encoding='utf-8') as f:
            f.write(qa_content)
    except Exception as e:
        return f"Q&A 저널 파일 작성 실패: {e}"

    # 3. Topic Summary 합성 (선택 사항)
    topic_info = ""
    if topic_name:
        topic_slug = slugify(topic_name)
        topic_file_path = os.path.join(root_dir, "topics", f"{topic_slug}.md")
        os.makedirs(os.path.dirname(topic_file_path), exist_ok=True)
        
        # 파일이 존재하면 누적 업데이트
        if os.path.exists(topic_file_path):
            try:
                with open(topic_file_path, 'r', encoding='utf-8') as f:
                    old_content = f.read()
                
                # 기존 콘텐츠 하단에 업데이트 추가
                synthesis_text = f"\n\n### 업데이트 ({date_str})\n{topic_update_text}"
                new_content = old_content + synthesis_text
                
                # timestamp 메타데이터 필드 갱신
                new_content = re.sub(
                    r'timestamp:.*', 
                    f'timestamp: "{now.isoformat()}"', 
                    new_content
                )
                
                with open(topic_file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                topic_info = f" 및 토픽 '{topic_slug}.md' 누적 합성"
            except Exception as e:
                return f"Q&A 저널은 작성되었으나 토픽 합성 중 실패: {e}"
        else:
            # 신규 토픽 생성
            topic_content = f"""---
type: TopicSummary
title: "{topic_name}"
description: "자동 생성된 토픽 정리본: {topic_name}"
tags: {tags_formatted}
timestamp: "{now.isoformat()}"
---

# {topic_name}

{topic_update_text or '내용을 입력하세요.'}
"""
            try:
                with open(topic_file_path, 'w', encoding='utf-8') as f:
                    f.write(topic_content)
                topic_info = f" 및 신규 토픽 '{topic_slug}.md' 생성"
            except Exception as e:
                return f"Q&A 저널은 작성되었으나 토픽 생성 중 실패: {e}"

    rel_qa_path = os.path.relpath(qa_file_path, root_dir)
    return (
        f"성공: 지식이 마크다운 파일로 영속화되었습니다.\n"
        f"- Q&A 저널: {rel_qa_path}{topic_info}{resource_info}\n\n"
        f"*주의: 사용자의 로컬 검토 완료 전입니다. AI 지식으로 최신화하려면 '인덱싱해줘'라고 요청하시거나 run_wiki_indexing 스킬을 호출해 주세요."
    )

def run_wiki_indexing() -> str:
    """
    로컬 마크다운 파일들을 스캔하여 최신 지식을 데이터베이스에 증분 인덱싱(임베딩)합니다.
    """
    from src.core.database.factory import DatabaseManager
    from src.indexing.domain.embedding import FakeEmbeddingService, OpenAIEmbeddingService, BGEM3EmbeddingService
    from src.core.config import EMBEDDING_PROVIDER, EMBEDDING_DIM, WIKI_DIR
    from src.indexing.application.service import WikiIndexer

    root_dir = WIKI_DIR
    try:
        if EMBEDDING_PROVIDER == "openai":
            embedding_service = OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
        elif EMBEDDING_PROVIDER == "bge-m3":
            embedding_service = BGEM3EmbeddingService()
        else:
            embedding_service = FakeEmbeddingService(dimension=EMBEDDING_DIM)
            
        db_manager = DatabaseManager()
        indexer = WikiIndexer(root_dir=root_dir, db_manager=db_manager, embedding_service=embedding_service)
        stats = indexer.run_indexing()
        db_manager.close()
        
        return f"성공: 데이터베이스 증분 인덱싱이 성공적으로 실행되었습니다.\n인덱싱 통계: {stats}"
    except Exception as e:
        return f"데이터베이스 인덱싱 수행 중 에러 발생: {e}"

# langchain_tool_spec_commit = {
#     "name": "commit_wiki_knowledge",
#     "description": "새로운 지식을 qa/ 저널 및 topics/ 문서에 마크다운 파일로 기록합니다.",
#     "func": commit_wiki_knowledge
# }
# langchain_tool_spec_indexing = {
#     "name": "run_wiki_indexing",
#     "description": "로컬 마크다운 지식들을 데이터베이스에 실시간으로 증분 인덱싱하여 AI가 참조할 수 있게 만듭니다.",
#     "func": run_wiki_indexing
# }
