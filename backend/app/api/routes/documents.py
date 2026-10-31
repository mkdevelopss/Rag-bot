from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.schemas.api import DocumentResponse, UploadBatchResponse, UploadError
from app.services.document_loader import SUPPORTED_EXTENSIONS
from app.services.document_service import DuplicateDocumentError
from app.services.authentication import require_admin, require_user

router = APIRouter(dependencies=[Depends(require_user)])
ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
}
GENERIC_MIME_TYPES = {"", "application/octet-stream"}


def validate_upload(filename: str, content_type: str, size: int, maximum: int) -> None:
    extension = Path(filename).suffix.lower()
    content_type = content_type.split(";", 1)[0].strip().lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{extension or 'unknown'}'. Use PDF, DOCX, TXT, MD, or CSV.")
    if content_type not in GENERIC_MIME_TYPES and content_type not in ALLOWED_MIME_TYPES[extension]:
        raise ValueError(f"File content type does not match the {extension} extension.")
    if size == 0:
        raise ValueError("The uploaded file is empty.")
    if size > maximum:
        raise ValueError(f"File exceeds the {maximum // (1024 * 1024)} MB upload limit.")


@router.post(
    "/upload",
    response_model=UploadBatchResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def upload_documents(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    settings = request.app.state.runtime_settings.get()
    service = request.app.state.document_service
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    documents = []
    errors = []
    for file in files:
        filename = file.filename or "uploaded-document"
        try:
            content = await file.read(max_bytes + 1)
            validate_upload(filename, file.content_type or "", len(content), max_bytes)
            document = await run_in_threadpool(service.register_upload, filename, content)
            background_tasks.add_task(service.process_document, document["document_id"])
            documents.append(DocumentResponse(**document))
        except DuplicateDocumentError as exc:
            errors.append(UploadError(filename=filename, error=str(exc)))
        except ValueError as exc:
            errors.append(UploadError(filename=filename, error=str(exc)))
        except Exception as exc:
            errors.append(UploadError(filename=filename, error=f"Processing failed: {exc}"))
        finally:
            await file.close()
    if not documents and errors:
        raise HTTPException(status_code=422, detail=[item.model_dump() for item in errors])
    return UploadBatchResponse(documents=documents, errors=errors)


@router.get("", response_model=list[DocumentResponse])
def list_documents(request: Request):
    return request.app.state.database.list_documents()


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, request: Request):
    document = request.app.state.database.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@router.delete(
    "/{document_id}", status_code=204, dependencies=[Depends(require_admin)]
)
def delete_document(
    document_id: str,
    request: Request,
):
    if not request.app.state.document_service.delete(document_id):
        raise HTTPException(status_code=404, detail="Document not found.")


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentResponse,
    dependencies=[Depends(require_admin)],
)
def reprocess_document(
    document_id: str,
    request: Request,
):
    document = request.app.state.document_service.reprocess(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document
