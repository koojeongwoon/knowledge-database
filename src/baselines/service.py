import hashlib
import json
import posixpath
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import yaml


LIVE_ROOTS = ("qa/", "topics/")
BASELINE_DRAFT_ROOT = "baseline-drafts"
BASELINE_ROOT = "baselines"


def _safe_segment(value: str, field: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value.strip()).strip("-.")
    if not normalized or normalized in {".", ".."}:
        raise ValueError(f"{field} 값이 올바르지 않습니다.")
    return normalized[:160]


def normalize_live_source_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    if normalized.startswith("../") or not normalized.endswith(".md"):
        raise ValueError("기준본 원본은 qa/ 또는 topics/ 아래의 Markdown이어야 합니다.")
    if not normalized.startswith(LIVE_ROOTS):
        raise ValueError("기준본 원본은 qa/ 또는 topics/의 일반 지식만 사용할 수 있습니다.")
    return normalized


def is_baseline_path(path: str) -> bool:
    normalized = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    return normalized == BASELINE_ROOT or normalized.startswith(BASELINE_ROOT + "/") or (
        normalized == BASELINE_DRAFT_ROOT or normalized.startswith(BASELINE_DRAFT_ROOT + "/")
    )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _find_value_error(error: Exception) -> ValueError | None:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ValueError):
            return current
        current = current.__cause__ or current.__context__
    return None


@dataclass(frozen=True)
class BaselineDraft:
    draft_id: str
    name: str
    version: str
    purpose: str
    source_paths: tuple[str, ...]
    source_hashes: dict[str, str]
    directory_path: str
    base_release_id: str | None


class BaselineRepository:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def create_draft(self, owner_id: str, draft: BaselineDraft) -> None:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_baseline_drafts (
                    draft_id, owner_id, name, version, purpose, base_release_id,
                    source_paths, source_hashes, directory_path
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """, (
                draft.draft_id, owner_id, draft.name, draft.version, draft.purpose,
                draft.base_release_id, list(draft.source_paths),
                json.dumps(draft.source_hashes, ensure_ascii=False), draft.directory_path,
            ))

    def get_pending_draft(self, owner_id: str, draft_id: str) -> BaselineDraft | None:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT draft_id, name, version, purpose, source_paths, source_hashes,
                       directory_path, base_release_id
                FROM knowledge_baseline_drafts
                WHERE owner_id = %s AND draft_id = %s AND status = 'pending'
            """, (owner_id, draft_id))
            row = cur.fetchone()
        if not row:
            return None
        hashes = row[5] if isinstance(row[5], dict) else json.loads(row[5])
        return BaselineDraft(
            draft_id=str(row[0]), name=row[1], version=row[2], purpose=row[3],
            source_paths=tuple(row[4]), source_hashes=hashes,
            directory_path=row[6], base_release_id=str(row[7]) if row[7] else None,
        )

    def confirm(self, owner_id: str, draft: BaselineDraft, release_id: str,
                release_dir: str, manifest_hash: str, materialize: Callable[[], None]) -> None:
        with self.db_manager.transaction() as cur:
            lock_key = f"knowledge-baseline:{owner_id}:{draft.name}:{draft.version}"
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
            cur.execute("""
                SELECT 1 FROM knowledge_baseline_drafts
                WHERE owner_id = %s AND draft_id = %s AND status = 'pending'
                FOR UPDATE
            """, (owner_id, draft.draft_id))
            if not cur.fetchone():
                raise ValueError("이미 처리되었거나 존재하지 않는 기준본 초안입니다.")
            if draft.base_release_id:
                cur.execute("""
                    SELECT 1 FROM knowledge_baseline_releases
                    WHERE owner_id = %s AND release_id = %s AND status = 'confirmed'
                """, (owner_id, draft.base_release_id))
                if not cur.fetchone():
                    raise ValueError("기준이 되는 확정본을 찾을 수 없습니다.")
            for source_path, expected_hash in draft.source_hashes.items():
                cur.execute("""
                    SELECT 1 FROM knowledge_documents
                    WHERE owner_id = %s AND file_path = %s AND content_hash = %s
                    LIMIT 1
                """, (owner_id, source_path, expected_hash))
                if not cur.fetchone():
                    raise ValueError(
                        f"최신 원본과 일치하는 인덱스가 없습니다. 먼저 해당 문서를 인덱싱하세요: {source_path}"
                    )

            materialize()
            cur.execute("""
                INSERT INTO knowledge_baseline_releases (
                    release_id, owner_id, name, version, purpose, base_release_id,
                    directory_path, manifest_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (release_id, owner_id, draft.name, draft.version, draft.purpose,
                  draft.base_release_id, release_dir, manifest_hash))
            for source_path in draft.source_paths:
                snapshot_path = f"{release_dir}/documents/{source_path}"
                cur.execute("""
                    INSERT INTO knowledge_baseline_documents (
                        release_id, owner_id, file_path, snapshot_path, chunk_index,
                        doc_type, title, description, tags, content, parent_content,
                        raw_frontmatter, content_hash, embedding
                    )
                    SELECT %s, %s, file_path, %s, chunk_index, doc_type, title,
                           description, tags, content, parent_content, raw_frontmatter,
                           content_hash, embedding
                    FROM knowledge_documents
                    WHERE owner_id = %s AND file_path = %s AND content_hash = %s
                """, (
                    release_id, owner_id, snapshot_path, owner_id, source_path,
                    draft.source_hashes[source_path],
                ))
                if cur.rowcount == 0:
                    raise ValueError(f"최신 원본과 일치하는 인덱스가 없습니다: {source_path}")
            cur.execute("""
                UPDATE knowledge_baseline_drafts
                SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP
                WHERE owner_id = %s AND draft_id = %s AND status = 'pending'
            """, (owner_id, draft.draft_id))
            if cur.rowcount != 1:
                raise ValueError("이미 처리되었거나 존재하지 않는 기준본 초안입니다.")


class BaselineService:
    def __init__(self, owner_id: str, storage, repository: BaselineRepository):
        if not owner_id or owner_id == "SYSTEM":
            raise ValueError("기준본은 인증된 사용자만 관리할 수 있습니다.")
        self.owner_id = owner_id
        self.storage = storage
        self.repository = repository

    def prepare(self, *, name: str, version: str, purpose: str,
                source_paths: list[str], base_release_id: str | None = None) -> dict[str, Any]:
        safe_name = _safe_segment(name, "name")
        safe_version = _safe_segment(version, "version")
        if not purpose.strip():
            raise ValueError("기준본의 사용 목적이 필요합니다.")
        paths = tuple(dict.fromkeys(normalize_live_source_path(path) for path in source_paths))
        if not paths:
            raise ValueError("기준본에 포함할 원본 경로가 필요합니다.")
        hashes: dict[str, str] = {}
        for path in paths:
            hashes[path] = _content_hash(self.storage.read_text(path))

        draft_id = str(uuid.uuid4())
        directory = f"{BASELINE_DRAFT_ROOT}/{draft_id}"
        draft = BaselineDraft(
            draft_id=draft_id, name=safe_name, version=safe_version,
            purpose=purpose.strip(), source_paths=paths, source_hashes=hashes,
            directory_path=directory, base_release_id=base_release_id,
        )
        manifest = {
            "kind": "KnowledgeBaselineDraft", "status": "pending",
            "draft_id": draft_id, "name": safe_name, "version": safe_version,
            "purpose": draft.purpose, "base_release_id": base_release_id,
            "sources": [{"path": path, "sha256": hashes[path]} for path in paths],
        }
        self.storage.write_text(f"{directory}/baseline.yaml", yaml.safe_dump(
            manifest, allow_unicode=True, sort_keys=False,
        ))
        self.repository.create_draft(self.owner_id, draft)
        return manifest | {"directory_path": directory}

    def confirm(self, draft_id: str) -> dict[str, Any]:
        draft = self.repository.get_pending_draft(self.owner_id, draft_id)
        if not draft:
            raise ValueError("확정할 수 있는 기준본 초안을 찾지 못했습니다.")
        for path, expected_hash in draft.source_hashes.items():
            if _content_hash(self.storage.read_text(path)) != expected_hash:
                raise ValueError(f"초안 생성 후 원본이 변경되었습니다. 초안을 다시 만드세요: {path}")

        release_id = str(uuid.uuid4())
        release_dir = f"{BASELINE_ROOT}/{draft.name}/{draft.version}"
        manifest = {
            "kind": "KnowledgeBaseline", "status": "confirmed",
            "release_id": release_id, "name": draft.name, "version": draft.version,
            "purpose": draft.purpose, "base_release_id": draft.base_release_id,
            "documents": [
                {"source_path": path, "snapshot_path": f"{release_dir}/documents/{path}",
                 "sha256": draft.source_hashes[path]}
                for path in draft.source_paths
            ],
        }
        manifest_text = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
        manifest_hash = _content_hash(manifest_text)
        materialized = False

        def materialize() -> None:
            nonlocal materialized
            if self.storage.exists(f"{release_dir}/baseline.yaml"):
                raise ValueError("같은 이름과 버전의 확정본 디렉터리가 이미 존재합니다.")
            materialized = True
            for path in draft.source_paths:
                self.storage.copy_file(path, f"{release_dir}/documents/{path}")
            self.storage.write_text(f"{release_dir}/baseline.yaml", manifest_text)

        try:
            self.repository.confirm(
                self.owner_id, draft, release_id, release_dir, manifest_hash, materialize,
            )
        except Exception as error:
            if materialized:
                try:
                    self.storage.delete_file(release_dir)
                except Exception as cleanup_error:
                    error.add_note(
                        f"기준본 확정 실패 후 스토리지 정리도 실패했습니다: {type(cleanup_error).__name__}"
                    )
            domain_error = _find_value_error(error)
            if domain_error is not None and domain_error is not error:
                raise domain_error from error
            raise
        return manifest | {"directory_path": release_dir, "manifest_sha256": manifest_hash}
