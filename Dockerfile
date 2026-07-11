FROM python:3.11-slim

# OS 패키지 최신화 및 빌드 종속성 설치 (psycopg2-binary 빌드 및 기타 용도)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pyproject.toml 및 README.md 복사
COPY pyproject.toml README.md ./

# 프로젝트 소스 코드 우선 복사 (hatchling 빌드가 소스를 찾을 수 있도록)
COPY src/ ./src/

# 의존성 패키지 및 프로젝트 빌드/설치
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .


# 지식 마크다운 문서들이 마운트될 디렉토리 생성
RUN mkdir -p /app/wiki

EXPOSE 8000

# FastAPI 기반 MCP SSE 서버 기동
CMD ["uvicorn", "src.api.mcp_server:app", "--host", "0.0.0.0", "--port", "8000"]
