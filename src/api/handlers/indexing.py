import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.api.exceptions import DatabaseException, WikiBaseException


@dataclass(frozen=True)
class IndexingRunApiHandler:
    database_factory: Callable[[], Any]
    embedding_factory: Callable[[], Any]
    indexer_factory: Callable[[Any, Any], Any]
    audit: Callable[..., None]

    def run(self, file_paths: Optional[List[str]], user_id: str) -> Dict[str, Any]:
        try:
            database = self.database_factory()
            database.connect()
        except Exception as exc:
            self.audit("VECTOR_INDEXING_RUN", "FAILED", user_id=user_id,
                       payload={"error": f"DB 연결 실패: {exc}"})
            raise DatabaseException(f"인덱싱 데이터베이스 연결 실패: {exc}") from exc

        try:
            stats = self.indexer_factory(database, self.embedding_factory()).run_indexing(
                file_paths=file_paths,
            )
            self.audit(
                action="VECTOR_INDEXING_RUN", status="SUCCESS", user_id=user_id,
                payload={"stats": stats, "file_paths": file_paths},
            )
            return stats
        except Exception as exc:
            self.audit("VECTOR_INDEXING_RUN", "FAILED", user_id=user_id,
                       payload={"error": str(exc)})
            raise WikiBaseException(f"인덱싱 수행 중 치명적 에러 발생: {exc}") from exc
        finally:
            database.close()


@dataclass(frozen=True)
class IndexingRetryApiHandler:
    database_factory: Callable[[], Any]
    repository_factory: Callable[[Any], Any]
    settings_factory: Callable[[], Any]
    run_indexing: Callable[..., str]
    config_context: Any

    def retry(self, limit: int, force: bool) -> Dict[str, Any]:
        database = self.database_factory()
        repository = self.repository_factory(database)
        try:
            jobs = repository.claim(limit=limit, force=force)
            if not jobs:
                return {"status": "empty", "processed": 0, "jobs": []}

            jobs_by_owner = defaultdict(list)
            for job in jobs:
                jobs_by_owner[job["owner_id"]].append(job["file_path"])

            processed = 0
            results = []
            for owner_id, file_paths in jobs_by_owner.items():
                settings = self.settings_factory()
                try:
                    stored_config = settings.get_runtime_config(owner_id)
                finally:
                    settings.db_manager.close()

                token = self.config_context.set({
                    "api_key": f"background:{owner_id}",
                    "user_id": owner_id,
                    **stored_config,
                })
                try:
                    response = json.loads(self.run_indexing(file_paths=file_paths))
                    if response.get("success"):
                        repository.complete(file_paths, owner_id=owner_id)
                        processed += len(file_paths)
                        results.append({
                            "owner_id": owner_id, "status": "success", "file_paths": file_paths,
                            "stats": response.get("data"),
                        })
                    else:
                        error = response.get("message") or "Indexing retry failed"
                        repository.fail(file_paths, error, owner_id=owner_id)
                        results.append({
                            "owner_id": owner_id, "status": "failed", "file_paths": file_paths,
                            "error": error,
                        })
                except Exception as owner_error:
                    repository.fail(file_paths, str(owner_error), owner_id=owner_id)
                    results.append({
                        "owner_id": owner_id, "status": "failed", "file_paths": file_paths,
                        "error": str(owner_error),
                    })
                finally:
                    self.config_context.reset(token)

            return {
                "status": "success" if processed == len(jobs) else "partial_failure",
                "processed": processed, "claimed": len(jobs), "results": results,
            }
        except Exception as exc:
            raise WikiBaseException(f"인덱싱 재시도 중 오류 발생: {exc}") from exc
        finally:
            database.close()
