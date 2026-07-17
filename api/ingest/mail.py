"""Semi-automatic email intake with a mandatory human confirmation gate.

Email ``From`` headers can be forged. The allow-list prevents accidental intake,
not deliberate attacks; a production deployment would also require DKIM/SPF
verification and malware scanning, which are outside this project's scope.
"""

from __future__ import annotations

import hashlib
import imaplib
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import duckdb

from ingest.database import table_exists
from ingest.errors import IngestError
from ingest.models import ValidationReport
from ingest.pipeline import import_rows, validate_file

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INBOX = REPO_ROOT / "data" / "inbox"
MAIL_STATUSES = (
    "pending_review",
    "blocked",
    "invalid_file",
    "confirmed",
    "rejected",
)
_mail_action_lock = threading.Lock()


@dataclass(frozen=True)
class IncomingAttachment:
    message_uid: str
    sender: str
    subject: str
    filename: str
    content: bytes
    received_at: datetime


class EmailSource(Protocol):
    def fetch_new(self) -> list[IncomingAttachment]: ...


@dataclass(frozen=True)
class PollResult:
    new_items: int = 0
    blocked: int = 0
    duplicates: int = 0
    invalid_files: int = 0


@dataclass(frozen=True)
class MailItem:
    item_id: str
    message_uid: str
    sender: str
    subject: str
    filename: str
    attachment_sha256: str
    received_at: datetime
    status: str
    valid_count: int
    error_count: int
    batch_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _message_received_at(message: Message) -> datetime:
    raw_date = message.get("Date")
    if raw_date:
        try:
            return _utc_naive(parsedate_to_datetime(raw_date))
        except (TypeError, ValueError, OverflowError):
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_mime_attachments(raw_message: bytes, message_uid: str) -> list[IncomingAttachment]:
    """Parse xlsx attachments without any network access."""
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    sender = parseaddr(str(message.get("From", "")))[1] or "unknown@local"
    subject = str(message.get("Subject", ""))
    received_at = _message_received_at(message)
    attachments: list[IncomingAttachment] = []
    for part in message.walk():
        filename = part.get_filename()
        if not filename or not filename.casefold().endswith(".xlsx"):
            continue
        content = part.get_payload(decode=True)
        if content is None:
            continue
        attachments.append(
            IncomingAttachment(
                message_uid=message_uid,
                sender=sender,
                subject=subject,
                filename=filename,
                content=content,
                received_at=received_at,
            )
        )
    return attachments


class ImapEmailSource:
    """Fetch UNSEEN messages; all imaplib operations stay in one thin method.

    Personal mailboxes can hold hundreds of unread messages, so each poll scans
    only the newest ``fetch_limit`` of them, downloads with BODY.PEEK[] (which
    does not set ``\\Seen``), and flags only messages that actually carried an
    .xlsx attachment — everything else keeps its unread status untouched.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        folder: str = "INBOX",
        fetch_limit: int = 25,
        timeout: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.folder = folder
        self.fetch_limit = fetch_limit
        self.timeout = timeout

    def fetch_new(self) -> list[IncomingAttachment]:
        attachments: list[IncomingAttachment] = []
        with imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout) as client:
            client.login(self.user, self.password)
            status, _ = client.select(self.folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select IMAP folder {self.folder}")
            status, search_data = client.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("Unable to search IMAP mailbox")
            for raw_uid in search_data[0].split()[-self.fetch_limit :]:
                status, fetched = client.uid("fetch", raw_uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise RuntimeError(f"Unable to fetch IMAP message {raw_uid!r}")
                raw_message = next(
                    (
                        item[1]
                        for item in fetched
                        if isinstance(item, tuple) and isinstance(item[1], bytes)
                    ),
                    None,
                )
                if raw_message is None:
                    raise RuntimeError(f"IMAP message {raw_uid!r} has no message body")
                parsed = parse_mime_attachments(
                    raw_message, raw_uid.decode("ascii", errors="replace")
                )
                if parsed:
                    attachments.extend(parsed)
                    client.uid("store", raw_uid, "+FLAGS", "\\Seen")
        return attachments


class DirectoryEmailSource:
    """Use a local inbox as an offline mailbox and retain processed demo files."""

    def __init__(self, inbox: Path = DEFAULT_INBOX) -> None:
        self.inbox = inbox

    @staticmethod
    def _destination(processed: Path, filename: str) -> Path:
        destination = processed / filename
        suffix = 1
        while destination.exists():
            destination = processed / f"{Path(filename).stem}.{suffix}{Path(filename).suffix}"
            suffix += 1
        return destination

    def fetch_new(self) -> list[IncomingAttachment]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        processed = self.inbox / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        attachments: list[IncomingAttachment] = []
        for path in sorted(self.inbox.glob("*.xlsx")):
            content = path.read_bytes()
            if "__" in path.name:
                sender, filename = path.name.split("__", 1)
                sender = sender.strip() or "local@demo"
            else:
                sender = "local@demo"
                filename = path.name
            attachments.append(
                IncomingAttachment(
                    message_uid=hashlib.sha256(content).hexdigest(),
                    sender=sender,
                    subject="本地目录接入",
                    filename=filename,
                    content=content,
                    received_at=datetime.fromtimestamp(
                        path.stat().st_mtime, tz=timezone.utc
                    ).replace(tzinfo=None),
                )
            )
            shutil.move(str(path), self._destination(processed, path.name))
        return attachments


def email_source_from_env() -> EmailSource:
    host = os.getenv("INGEST_MAIL_HOST", "").strip()
    port = os.getenv("INGEST_MAIL_PORT", "").strip()
    user = os.getenv("INGEST_MAIL_USER", "").strip()
    password = os.getenv("INGEST_MAIL_PASSWORD", "")
    if host and port and user and password:
        try:
            parsed_port = int(port)
        except ValueError:
            return DirectoryEmailSource()
        return ImapEmailSource(
            host=host,
            port=parsed_port,
            user=user,
            password=password,
            folder=os.getenv("INGEST_MAIL_FOLDER", "INBOX").strip() or "INBOX",
        )
    return DirectoryEmailSource()


def allowed_senders_from_env() -> set[str]:
    return {
        sender.strip().casefold()
        for sender in os.getenv("INGEST_MAIL_ALLOWED_SENDERS", "").split(",")
        if sender.strip()
    }


def mail_poll_seconds_from_env() -> int:
    try:
        seconds = int(os.getenv("INGEST_MAIL_POLL_SECONDS", "0"))
    except ValueError:
        return 0
    return seconds if seconds > 0 else 0


def ensure_mail_table(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the mail-owned table without changing T6b's table initializer."""
    statuses = ", ".join(f"'{status}'" for status in MAIL_STATUSES)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS ingest_mail_item ("
        "item_id VARCHAR PRIMARY KEY, message_uid VARCHAR NOT NULL, "
        "sender VARCHAR NOT NULL, subject VARCHAR NOT NULL, filename VARCHAR NOT NULL, "
        "attachment_sha256 VARCHAR NOT NULL, received_at TIMESTAMP NOT NULL, "
        f"status VARCHAR NOT NULL CHECK (status IN ({statuses})), "
        "valid_count INTEGER NOT NULL, error_count INTEGER NOT NULL, "
        "batch_id VARCHAR, file_blob BLOB, error_code VARCHAR, error_message VARCHAR, "
        "created_at TIMESTAMP NOT NULL, UNIQUE (message_uid, attachment_sha256))"
    )


def _duplicate_exists(
    connection: duckdb.DuckDBPyConnection, message_uid: str, attachment_sha256: str
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM ingest_mail_item WHERE message_uid = ? AND attachment_sha256 = ?",
        [message_uid, attachment_sha256],
    ).fetchone()
    return row is not None


def _insert_item(
    connection: duckdb.DuckDBPyConnection,
    attachment: IncomingAttachment,
    attachment_sha256: str,
    *,
    status: str,
    valid_count: int = 0,
    error_count: int = 0,
    file_blob: bytes | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO ingest_mail_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            f"MAIL-{uuid4().hex}",
            attachment.message_uid,
            attachment.sender,
            attachment.subject,
            attachment.filename,
            attachment_sha256,
            _utc_naive(attachment.received_at),
            status,
            valid_count,
            error_count,
            None,
            file_blob,
            error_code,
            error_message,
            datetime.now(timezone.utc).replace(tzinfo=None),
        ],
    )


def poll_mailbox(
    connection: duckdb.DuckDBPyConnection,
    source: EmailSource,
    allowed_senders: set[str],
) -> PollResult:
    """Fetch and validate attachments, but never confirm or import them."""
    ensure_mail_table(connection)
    new_items = blocked = duplicates = invalid_files = 0
    normalized_allowed = {sender.strip().casefold() for sender in allowed_senders}
    for attachment in source.fetch_new():
        attachment_sha256 = hashlib.sha256(attachment.content).hexdigest()
        if _duplicate_exists(connection, attachment.message_uid, attachment_sha256):
            duplicates += 1
            continue
        if attachment.sender.strip().casefold() not in normalized_allowed:
            _insert_item(
                connection,
                attachment,
                attachment_sha256,
                status="blocked",
                error_code="sender_not_allowed",
                error_message="发件人不在接入白名单",
            )
            blocked += 1
            continue
        try:
            if not attachment.filename.casefold().endswith(".xlsx"):
                raise IngestError("unsupported_file_type", "仅支持 .xlsx 文件")
            report = validate_file(
                connection,
                attachment.content,
                filename=attachment.filename,
            )
        except IngestError as error:
            _insert_item(
                connection,
                attachment,
                attachment_sha256,
                status="invalid_file",
                error_code=error.code,
                error_message=error.message,
            )
            invalid_files += 1
            continue
        _insert_item(
            connection,
            attachment,
            attachment_sha256,
            status="pending_review",
            valid_count=report.valid_count,
            error_count=report.error_count,
            file_blob=attachment.content,
        )
        new_items += 1
    return PollResult(new_items, blocked, duplicates, invalid_files)


def _row_to_item(row: tuple[object, ...]) -> MailItem:
    return MailItem(
        item_id=str(row[0]),
        message_uid=str(row[1]),
        sender=str(row[2]),
        subject=str(row[3]),
        filename=str(row[4]),
        attachment_sha256=str(row[5]),
        received_at=row[6],
        status=str(row[7]),
        valid_count=int(row[8]),
        error_count=int(row[9]),
        batch_id=str(row[10]) if row[10] is not None else None,
        error_code=str(row[11]) if row[11] is not None else None,
        error_message=str(row[12]) if row[12] is not None else None,
        created_at=row[13],
    )


def list_mail_items(connection: duckdb.DuckDBPyConnection) -> list[MailItem]:
    if not table_exists(connection, "ingest_mail_item"):
        return []
    rows = connection.execute(
        "SELECT item_id, message_uid, sender, subject, filename, attachment_sha256, "
        "received_at, status, valid_count, error_count, batch_id, error_code, "
        "error_message, created_at FROM ingest_mail_item "
        "ORDER BY created_at DESC, item_id DESC"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def get_mail_item(connection: duckdb.DuckDBPyConnection, item_id: str) -> MailItem:
    if not table_exists(connection, "ingest_mail_item"):
        raise IngestError("mail_item_not_found", "邮件接入条目不存在", status_code=404)
    row = connection.execute(
        "SELECT item_id, message_uid, sender, subject, filename, attachment_sha256, "
        "received_at, status, valid_count, error_count, batch_id, error_code, "
        "error_message, created_at FROM ingest_mail_item WHERE item_id = ?",
        [item_id],
    ).fetchone()
    if row is None:
        raise IngestError("mail_item_not_found", "邮件接入条目不存在", status_code=404)
    return _row_to_item(row)


def validate_mail_item(connection: duckdb.DuckDBPyConnection, item_id: str) -> ValidationReport:
    item = get_mail_item(connection, item_id)
    if item.status != "pending_review":
        raise IngestError(
            "mail_item_not_pending", "只有待确认邮件项可查看实时校验报告", status_code=409
        )
    row = connection.execute(
        "SELECT file_blob FROM ingest_mail_item WHERE item_id = ?", [item_id]
    ).fetchone()
    if row is None or row[0] is None:
        raise IngestError("mail_blob_missing", "邮件附件内容不可用", status_code=409)
    return validate_file(connection, bytes(row[0]), filename=item.filename)


def confirm_mail_item(
    connection: duckdb.DuckDBPyConnection, item_id: str
) -> tuple[str, ValidationReport]:
    """Revalidate current DB state, then import only rows still valid after human action."""
    with _mail_action_lock:
        report = validate_mail_item(connection, item_id)
        if not report.valid_rows:
            raise IngestError(
                "no_valid_rows", "重新校验后没有仍然合法的行，条目保持待确认", status_code=409
            )
        batch_id = import_rows(connection, report)
        connection.execute(
            "UPDATE ingest_mail_item SET status = 'confirmed', batch_id = ?, "
            "valid_count = ?, error_count = ? WHERE item_id = ? AND status = 'pending_review'",
            [batch_id, report.valid_count, report.error_count, item_id],
        )
        return batch_id, report


def reject_mail_item(connection: duckdb.DuckDBPyConnection, item_id: str) -> None:
    with _mail_action_lock:
        item = get_mail_item(connection, item_id)
        if item.status != "pending_review":
            raise IngestError("mail_item_not_pending", "只有待确认邮件项可以拒绝", status_code=409)
        connection.execute(
            "UPDATE ingest_mail_item SET status = 'rejected' "
            "WHERE item_id = ? AND status = 'pending_review'",
            [item_id],
        )
