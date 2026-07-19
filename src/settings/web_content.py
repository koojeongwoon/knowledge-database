from typing import Awaitable, Callable, Optional
from urllib.parse import quote

from fastapi import APIRouter, Cookie, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field


class InboxLinkPayload(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: Optional[str] = Field(default=None, max_length=300)
    note: Optional[str] = Field(default=None, max_length=2000)


def create_content_router(
    authenticate: Callable[[Optional[str], Optional[str]], Awaitable[str]],
    inbox_service_factory: Callable[[str], object],
    document_service_factory: Callable[[str], object],
    max_upload_bytes: int,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/inbox")
    async def list_inbox_items(
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        try:
            return {"items": inbox_service_factory(owner_id).list_items()}
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/inbox/links", status_code=status.HTTP_201_CREATED)
    async def create_inbox_link(
        payload: InboxLinkPayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        try:
            return inbox_service_factory(owner_id).add_link(payload.url, payload.title, payload.note)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/inbox/files", status_code=status.HTTP_201_CREATED)
    async def create_inbox_file(
        file: UploadFile = File(...),
        note: Optional[str] = Form(default=None, max_length=2000),
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        content = await file.read(max_upload_bytes + 1)
        try:
            return inbox_service_factory(owner_id).add_file(
                file.filename or "file", content, file.content_type, note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            await file.close()

    @router.get("/api/inbox/{item_id}/file")
    async def download_inbox_file(
        item_id: str,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        try:
            item, content = inbox_service_factory(owner_id).read_file(item_id)
            filename = quote(item.get("filename", "download"))
            return Response(
                content,
                media_type=item.get("content_type") or "application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
            )
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.") from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/documents")
    async def list_documents(
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        try:
            return {"documents": document_service_factory(owner_id).list_documents()}
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/documents/{file_path:path}")
    async def read_document(
        file_path: str,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        try:
            return document_service_factory(owner_id).read_document(file_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.") from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
