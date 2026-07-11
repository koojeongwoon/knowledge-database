FROM python:3.11-slim

# OS 패키지 최신화 및 빌드 종속성 설치 (psycopg2-binary 빌드 및 기타 용도)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pyproject.toml 및 README.md 복사
COPY pyproject.toml README.md ./

# 의존성 패키지 우선 설치 (캐시 최적화)
# hatchling 빌드 시스템을 통해 프로젝트 의존성을 빌드/설치합니다.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# 프로젝트 소스 코드 및 디렉토리 복사
COPY src/ ./src/

# 지식 마크다운 문서들이 마운트될 디렉토리 생성
RUN mkdir -p /app/wiki

EXPOSE 8000

# FastAPI 기반 MCP SSE 서버 기동
CMD ["uvicorn", "src.api.mcp_server:app", "--host", "0.0.0.0", "--port", "8000"]
