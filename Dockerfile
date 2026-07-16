FROM python:3.11-slim

# OS 패키지 최신화 및 빌드 종속성 설치 (psycopg2-binary 빌드 및 기타 용도)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pyproject.toml 및 README.md 복사
COPY pyproject.toml README.md ./

# 의존성만 미리 설치하기 위해 빈 패키지를 만들어 캐싱 레이어 형성
RUN mkdir src && touch src/__init__.py && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# 실제 소스 코드 복사 후, 의존성 제외하고 프로젝트 최신화 (다음 빌드 시 1초 내로 통과)
COPY src/ ./src/
COPY main.py tests/search_quality_development.json tests/search_quality_gates.json ./
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000

# FastAPI 기반 MCP 서버 기동
CMD ["uvicorn", "src.api.mcp_server:app", "--host", "0.0.0.0", "--port", "8000"]
