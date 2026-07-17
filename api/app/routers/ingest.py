"""Two-step Excel ingestion API with deterministic validation and batch rollback."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from agent.llm import DeepSeekClient, LLMConfigurationError
from app.deps import get_db, get_llm, get_read_write_db
from app.schemas import (
    IngestBatch,
    IngestConfirmRequest,
    IngestImportResult,
    IngestMailConfig,
    IngestMailConfirmResult,
    IngestMailItem,
    IngestMailItemDetail,
    IngestMailPollResult,
    IngestMailRejectResult,
    IngestRollbackRequest,
    IngestRollbackResult,
    IngestTemplatePreview,
    IngestTemplateSaveRequest,
    IngestTemplateState,
    IngestValidationReport,
)
from ingest.database import TARGET_COLUMNS, table_exists
from ingest.errors import IngestError
from ingest.models import ValidationReport
from ingest.mail import (
    EmailSource,
    ImapEmailSource,
    allowed_senders_from_env,
    confirm_mail_item,
    email_source_from_env,
    get_mail_item,
    list_mail_items,
    mail_poll_seconds_from_env,
    poll_mailbox,
    reject_mail_item,
    validate_mail_item,
)
from ingest.pipeline import import_rows, rollback_batch, validate_file
from ingest.templates import (
    MappingSuggester,
    deterministic_mapping,
    get_template,
    read_sample,
    save_template,
    suggest_mapping,
)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
ReadDb = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]
WriteDb = Annotated[duckdb.DuckDBPyConnection, Depends(get_read_write_db)]
TOKEN_TTL_SECONDS = 15 * 60
MAX_ERRORS_RETURNED = 500
_validation_tokens: dict[str, tuple[float, ValidationReport]] = {}
_token_lock = threading.Lock()


class DeepSeekMappingSuggester:
    """Use the LLM only to suggest unresolved configuration-time column mappings."""

    def __init__(self, llm: DeepSeekClient) -> None:
        self.llm = llm

    def suggest(
        self, sample_columns: list[str], target_columns: list[str]
    ) -> dict[str, str | None]:
        prompt = (
            "为 Excel 列映射到 open_po 提供建议。只返回 JSON 对象；键必须来自目标列，"
            "值必须严格复制一个源列名或为 null。\n"
            f"目标列: {json.dumps(target_columns, ensure_ascii=False)}\n"
            f"源列: {json.dumps(sample_columns, ensure_ascii=False)}"
        )
        try:
            content = self.llm.chat(
                [
                    {"role": "system", "content": "你是谨慎的供应链数据列映射助手。"},
                    {"role": "user", "content": prompt},
                ],
                timeout=20,
            ).content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            payload: Any = json.loads(content)
            if not isinstance(payload, dict):
                return {}
            return {
                str(key): value if isinstance(value, str) else None
                for key, value in payload.items()
            }
        except Exception:
            # Suggestions are optional and never block deterministic preview.
            return {}


def get_mapping_suggester() -> MappingSuggester | None:
    """Wrap get_llm while silently degrading when no provider key is configured."""
    try:
        return DeepSeekMappingSuggester(get_llm())
    except LLMConfigurationError:
        return None


Suggester = Annotated[MappingSuggester | None, Depends(get_mapping_suggester)]


def _http_error(error: IngestError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


async def _xlsx_bytes(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or "upload.xlsx"
    if not filename.casefold().endswith(".xlsx"):
        raise _http_error(IngestError("unsupported_file_type", "仅支持 .xlsx 文件"))
    return filename, await file.read()


def _store_validation(report: ValidationReport) -> str:
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _token_lock:
        expired = [
            key
            for key, (created, _) in _validation_tokens.items()
            if now - created > TOKEN_TTL_SECONDS
        ]
        for key in expired:
            _validation_tokens.pop(key, None)
        _validation_tokens[token] = (now, report)
    return token


def _consume_validation(token: str) -> ValidationReport:
    with _token_lock:
        stored = _validation_tokens.pop(token, None)
    if stored is None or time.monotonic() - stored[0] > TOKEN_TTL_SECONDS:
        raise IngestError(
            "validation_token_invalid",
            "校验令牌无效或已过期，请重新上传并校验",
        )
    return stored[1]


@router.post("/template/preview", response_model=IngestTemplatePreview)
async def preview_template(
    file: Annotated[UploadFile, File()], suggester: Suggester
) -> dict[str, Any]:
    _, file_bytes = await _xlsx_bytes(file)
    try:
        columns = read_sample(file_bytes)
        deterministic = deterministic_mapping(columns)
        suggested = suggest_mapping(columns, suggester)
    except IngestError as error:
        raise _http_error(error) from error
    return {
        "source_columns": columns,
        "suggested_mapping": suggested,
        "suggestion_sources": {
            target: (
                "deterministic"
                if deterministic[target] is not None
                else "llm"
                if suggested[target] is not None
                else None
            )
            for target in TARGET_COLUMNS
        },
    }


@router.post("/template", response_model=IngestTemplateState)
def register_template(request: IngestTemplateSaveRequest, connection: WriteDb) -> dict[str, Any]:
    try:
        save_template(connection, request.mapping)
        current = get_template(connection)
    except IngestError as error:
        raise _http_error(error) from error
    assert current is not None
    mapping, created_at = current
    return {
        "exists": True,
        "target_table": "open_po",
        "mapping": mapping,
        "created_at": created_at.isoformat(),
    }


@router.get("/template", response_model=IngestTemplateState)
def current_template(connection: ReadDb) -> dict[str, Any]:
    current = get_template(connection)
    if current is None:
        return {"exists": False, "target_table": "open_po"}
    mapping, created_at = current
    return {
        "exists": True,
        "target_table": "open_po",
        "mapping": mapping,
        "created_at": created_at.isoformat(),
    }


@router.post("/validate", response_model=IngestValidationReport)
async def validate_upload(
    file: Annotated[UploadFile, File()], connection: ReadDb
) -> dict[str, Any]:
    filename, file_bytes = await _xlsx_bytes(file)
    try:
        report = validate_file(connection, file_bytes, filename=filename)
    except IngestError as error:
        raise _http_error(error) from error
    token = _store_validation(report)
    return {
        "validation_token": token,
        "filename": filename,
        "total_rows": report.total_rows,
        "valid_count": report.valid_count,
        "error_count": report.error_count,
        # Cap the detail list so a mostly-bad 50k-row file cannot blow up the
        # response; error_count still reports the true total.
        "errors": [asdict(error) for error in report.errors[:MAX_ERRORS_RETURNED]],
        "preview": [
            {**asdict(row), "eta_date": row.eta_date.isoformat()} for row in report.valid_rows[:20]
        ],
    }


@router.post("/confirm", response_model=IngestImportResult)
def confirm_import(request: IngestConfirmRequest, connection: WriteDb) -> dict[str, Any]:
    try:
        report = _consume_validation(request.validation_token)
        batch_id = import_rows(connection, report)
    except IngestError as error:
        raise _http_error(error) from error
    except Exception as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "import_failed", "message": "导入事务失败，未写入任何行"},
        ) from error
    return {"batch_id": batch_id, "row_count": report.valid_count}


@router.post("/rollback", response_model=IngestRollbackResult)
def rollback(request: IngestRollbackRequest, connection: WriteDb) -> dict[str, Any]:
    try:
        deleted_count = rollback_batch(connection, request.batch_id)
    except IngestError as error:
        raise _http_error(error) from error
    return {"batch_id": request.batch_id, "deleted_count": deleted_count}


@router.get("/batches", response_model=list[IngestBatch])
def batches(connection: ReadDb) -> list[dict[str, Any]]:
    if not table_exists(connection, "ingest_batch"):
        return []
    rows = connection.execute(
        "SELECT batch_id, filename, row_count, created_at FROM ingest_batch "
        "ORDER BY created_at DESC, batch_id DESC"
    ).fetchall()
    return [
        {
            "batch_id": str(row[0]),
            "filename": str(row[1]),
            "row_count": int(row[2]),
            "created_at": row[3].isoformat(),
        }
        for row in rows
    ]


def get_email_source() -> EmailSource:
    return email_source_from_env()


def get_allowed_mail_senders() -> set[str]:
    return allowed_senders_from_env()


MailSource = Annotated[EmailSource, Depends(get_email_source)]
AllowedMailSenders = Annotated[set[str], Depends(get_allowed_mail_senders)]


def _mail_item_payload(item: Any) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "message_uid": item.message_uid,
        "sender": item.sender,
        "subject": item.subject,
        "filename": item.filename,
        "attachment_sha256": item.attachment_sha256,
        "received_at": item.received_at.isoformat(),
        "status": item.status,
        "valid_count": item.valid_count,
        "error_count": item.error_count,
        "batch_id": item.batch_id,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "created_at": item.created_at.isoformat(),
    }


def _validation_snapshot(report: ValidationReport) -> dict[str, Any]:
    returned_errors = report.errors[:MAX_ERRORS_RETURNED]
    return {
        "filename": report.filename,
        "total_rows": report.total_rows,
        "valid_count": report.valid_count,
        "error_count": report.error_count,
        "errors_truncated": report.error_count > len(returned_errors),
        "errors": [asdict(error) for error in returned_errors],
        "preview": [
            {**asdict(row), "eta_date": row.eta_date.isoformat()} for row in report.valid_rows[:20]
        ],
    }


@router.post("/mail/poll", response_model=IngestMailPollResult)
def poll_mail(
    connection: WriteDb,
    source: MailSource,
    allowed_senders: AllowedMailSenders,
) -> dict[str, int]:
    try:
        result = poll_mailbox(connection, source, allowed_senders)
    except IngestError as error:
        raise _http_error(error) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail={"code": "mail_poll_failed", "message": "收取邮件失败，请检查邮件源配置"},
        ) from error
    return asdict(result)


@router.get("/mail/items", response_model=list[IngestMailItem])
def mail_items(connection: ReadDb) -> list[dict[str, Any]]:
    return [_mail_item_payload(item) for item in list_mail_items(connection)]


@router.get("/mail/items/{item_id}", response_model=IngestMailItemDetail)
def mail_item_detail(item_id: str, connection: ReadDb) -> dict[str, Any]:
    try:
        item = get_mail_item(connection, item_id)
        report = (
            validate_mail_item(connection, item_id) if item.status == "pending_review" else None
        )
    except IngestError as error:
        raise _http_error(error) from error
    return {
        **_mail_item_payload(item),
        "fresh_report": _validation_snapshot(report) if report is not None else None,
    }


@router.post("/mail/items/{item_id}/confirm", response_model=IngestMailConfirmResult)
def confirm_mail(item_id: str, connection: WriteDb) -> dict[str, Any]:
    try:
        batch_id, report = confirm_mail_item(connection, item_id)
    except IngestError as error:
        raise _http_error(error) from error
    except Exception as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "mail_import_failed", "message": "邮件导入事务失败，未确认该条目"},
        ) from error
    return {
        "batch_id": batch_id,
        "row_count": report.valid_count,
        "fresh_report": _validation_snapshot(report),
    }


@router.post("/mail/items/{item_id}/reject", response_model=IngestMailRejectResult)
def reject_mail(item_id: str, connection: WriteDb) -> dict[str, str]:
    try:
        reject_mail_item(connection, item_id)
    except IngestError as error:
        raise _http_error(error) from error
    return {"item_id": item_id, "status": "rejected"}


@router.get("/mail/config", response_model=IngestMailConfig)
def mail_config(source: MailSource, allowed_senders: AllowedMailSenders) -> dict[str, Any]:
    poll_seconds = mail_poll_seconds_from_env()
    return {
        "source": "imap" if isinstance(source, ImapEmailSource) else "directory",
        "scheduled_poll_enabled": poll_seconds > 0,
        "poll_seconds": poll_seconds,
        "allowed_senders_configured": bool(allowed_senders),
    }
