import re
import yaml
import hashlib
from typing import Dict, Any, List

def parse_markdown_file(file_path: str) -> Dict[str, Any]:
    """
    마크다운 파일을 읽어 YAML Frontmatter와 본문(body), 그리고 전체 파일의 SHA-256 해시를 리턴합니다.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    pattern = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
    match = pattern.match(content)
    
    frontmatter = {}
    body = content
    
    if match:
        yaml_content = match.group(1)
        try:
            frontmatter = yaml.safe_load(yaml_content) or {}
        except Exception as e:
            print(f"Warning: Failed to parse YAML frontmatter in {file_path}. Error: {e}")
        
        body = content[match.end():]
        
    return {
        "frontmatter": frontmatter,
        "body": body.strip(),
        "content_hash": content_hash
    }

def extract_wiki_links(body: str) -> List[str]:
    """
    본문에서 [[WikiLink]] 형식의 링크 대상 단어들을 추출합니다.
    """
    pattern = re.compile(r'\[\[(.*?)\]\]')
    # 파이프(|) 기호가 포함된 경우(예: [[이름|별칭]]), 실제 타겟명은 파이프 앞부분임
    raw_links = pattern.findall(body)
    cleaned_links = []
    for link in raw_links:
        target = link.split('|')[0].strip()
        if target:
            cleaned_links.append(target)
    return list(set(cleaned_links))

def split_markdown_by_headers(body: str) -> List[Dict[str, Any]]:
    """
    마크다운 문서를 헤더(H2: '##', H3: '###') 기준으로 쪼개어 중대형 단락(Parent Chunk)을 반환합니다.
    만약 헤더가 하나도 없는 단순 문서라면 전체 본문을 단일 단락으로 반환합니다.
    """
    lines = body.split('\n')
    chunks = []
    current_header = "Intro"
    current_lines = []
    
    # 헤더 매칭 정규식 (## 또는 ###)
    header_pattern = re.compile(r'^(##|###)\s+(.*)$')
    
    for line in lines:
        match = header_pattern.match(line.strip())
        if match:
            # 기존 축적된 내용이 있다면 저장
            accumulated = '\n'.join(current_lines).strip()
            if accumulated or len(chunks) == 0:
                chunks.append({
                    "header": current_header,
                    "content": accumulated if accumulated else "Intro Content"
                })
            current_header = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
            
    # 마지막 남은 단락 저장
    accumulated = '\n'.join(current_lines).strip()
    if accumulated or len(chunks) == 0:
        chunks.append({
            "header": current_header,
            "content": accumulated
        })
        
    return chunks

def chunk_text(text: str, max_chars: int = 300, overlap: int = 50) -> List[str]:
    """
    부모 단락의 본문을 200~300자 단위의 작은 자식 조각(Child Chunk)으로 분할합니다.
    슬라이딩 윈도우 기반으로 단락 간 문맥 보완을 위해 overlap을 둡니다.
    """
    if len(text) <= max_chars:
        return [text]
        
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        chunks.append(chunk)
        start += (max_chars - overlap)
        
    return chunks
