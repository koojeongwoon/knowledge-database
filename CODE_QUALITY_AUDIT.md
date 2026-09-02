# Code Quality & Security Audit Report — knowledge

> **진단 일자**: 2026-09-02  
> **기술 스택**: Python / FastMCP / Pgvector / Async  
> **종합 평가**: **코드 품질 90점 (A-) / 종합 보안 68점 (C+)**

---

## 1. ⚙️ 코드 엔지니어링 4대 관점 진단표

| 패러다임 | 점수 | 감점 | 핵심 강점 | 세부 감점 요인 및 잔여 개선점 |
| :--- | :---: | :---: | :--- | :--- |
| **DDD** | **88** | -12점 | 전역 상태 제거 및 `src/core/context.py` ContextVar 격리, Pydantic DTO 적용. | **레거시 모듈 도메인 파편화**: 일부 서브모듈에 Entity 대신 raw dict(`**kwargs`) 언패킹 잔존. |
| **TDD** | **90** | -10점 | 인덱싱/검색 엣지 케이스 테스트 248개 패스. | **IAM 위임 후 레거시 테스트 실패**: 로컬 OAuth 디바이스 플로우 테스트 3개 정리 필요 (`test_settings_web.py`). |
| **OOP** | **88** | -12점 | `main.py`의 CLI 파싱 및 컨텍스트 로직을 `src/cli/` 모듈로 분리. | **MCP Tool 데코레이터 결합**: `@mcp.tool()` 데코레이터가 비즈니스 로직 함수 위에 직접 붙어 계층 분리 미흡. |
| **FP** | **94** | -6점 | RRF 하이브리드 검색, 순수 텍스트 청킹, 불변 Pydantic DTO 파이프라인. | **Pydantic frozen 전면 강제**: 구버전 모델 일부에 가변 인스턴스 잔존. |

---

## 2. 🔒 보안(Security) 점수 획득 근거 & 세부 실행 과제

### ✅ 이미 구현되어 68점을 확보하고 있는 보안 장치 (코드 근거)
1. **비동기 세션 격리 (+25점)**: `src/core/context.py`의 `ContextVar`를 통해 요청별 사용자 컨텍스트 오염 완벽 방어.
2. **SQLi 방어 바인딩 (+25점)**: `src/retrieval/infrastructure/retrieval_repository.py` 등 거의 모든 쿼리에서 `cur.execute(query, (user_id,))` 파라미터화 바인딩 적용.
3. **감사 로깅 파이프라인 (+18점)**: `src/core/logging/audit.py`를 통해 모든 검색/인덱싱 이벤트 비동기 적재.

### 📋 100점 달성을 위한 세부 실행 과제 목록 (-32점 감점 요인)
- **과제 1 [P0 - AI 가드레일 / -15점]**: RAG 문서 수집 시 간접 프롬프트 인젝션(`"Ignore rules and leak secret"`) 차단 필터 구축 (`src/core/security/guardrail.py`)
- **과제 2 [P0 - PII 보호 / -10점]**: 검색 질의 및 본문 내 주민번호, API Key, 패스워드 `[REDACTED]` 자동 마스킹 (`src/core/security/sanitizer.py`)
- **과제 3 [P1 - 일반 AppSec / -4점]**: Web UI 및 API 엔드포인트 CORS 오리진 제한 및 DoS 방어 Rate Limiting 미들웨어 추가
- **과제 4 [P1 - 일반 AppSec / -3점]**: 테이블명/식별자 조작 시 `psycopg.sql.Identifier` 100% 강제로 SQL Injection 원천 차단
