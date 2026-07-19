from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class KnowledgeCommitApiHandler:
    runtime_factory: Callable[[], Any]
    audit: Callable[..., None]

    def commit(
        self, *, user_id: str, title: str, description: str, tags: List[str], content: str,
        topic_name: Optional[str], topic_update_text: Optional[str],
        image_paths: Optional[List[str]], resource_paths: Optional[List[str]],
        resource_summaries: Optional[List[Dict[str, Any]]], visibility: str,
    ) -> Dict[str, Any]:
        runtime = None
        try:
            runtime = self.runtime_factory()
            result = runtime.coordinator.commit(
                title=title, description=description, tags=tags, content=content,
                topic_name=topic_name, topic_update_text=topic_update_text,
                image_paths=image_paths, resource_paths=resource_paths,
                resource_summaries=resource_summaries, visibility=visibility,
            )
            self.audit(
                action="KNOWLEDGE_COMMIT", status="SUCCESS", user_id=user_id,
                payload={
                    "title": title, "qa_path": result["qa_file_path"],
                    "topic_name": topic_name, "topic_path": result["topic_file_path"],
                    "resources_count": len(result["all_resources"]),
                },
            )
            return {
                "qa_file_path": result["qa_file_path"],
                "topic_file_path": result["topic_file_path"],
                "details": result["details"],
                "indexing": result["indexing"],
            }
        except Exception as exc:
            self.audit("KNOWLEDGE_COMMIT", "FAILED", user_id=user_id,
                       payload={"title": title, "error": str(exc)})
            raise
        finally:
            if runtime is not None:
                runtime.close()
