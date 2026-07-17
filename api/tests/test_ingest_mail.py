"""Smoke tests for semi-automatic email intake and its human confirmation gate."""

from __future__ import annotations

import asyncio
import shutil
from datetime import date, datetime
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.deps import get_db, get_read_write_db
from ingest.errors import IngestError
from ingest.mail import (
    DirectoryEmailSource,
    IncomingAttachment,
    confirm_mail_item,
    get_mail_item,
    list_mail_items,
    parse_mime_attachments,
    poll_mailbox,
    reject_mail_item,
)
from ingest.pipeline import rollback_batch
from ingest.templates import read_sample, save_template, suggest_mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DB = REPO_ROOT / "data" / "chainpilot.duckdb"
FIXTURES = REPO_ROOT / "data" / "fixtures" / "ingest"
CANONICAL_MAPPING = {
    "po_id": "po_id",
    "material_pn": "material_pn",
    "supplier_id": "supplier_id",
    "qty": "qty",
    "eta_date": "eta_date",
}


@pytest.fixture
def db_copy(tmp_path: Path) -> Path:
    if not REAL_DB.is_file():
        pytest.skip("real DuckDB fixture is unavailable")
    destination = tmp_path / "chainpilot.duckdb"
    shutil.copyfile(REAL_DB, destination)
    return destination


def workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def attachment(
    content: bytes,
    *,
    uid: str = "uid-1",
    sender: str = "planner@example.com",
    filename: str = "orders.xlsx",
) -> IncomingAttachment:
    return IncomingAttachment(
        message_uid=uid,
        sender=sender,
        subject="Open PO update",
        filename=filename,
        content=content,
        received_at=datetime(2026, 7, 17, 9, 30),
    )


class FakeSource:
    def __init__(self, attachments: list[IncomingAttachment]) -> None:
        self.attachments = attachments
        self.calls = 0

    def fetch_new(self) -> list[IncomingAttachment]:
        self.calls += 1
        return list(self.attachments)


def register_fixture_template(connection: duckdb.DuckDBPyConnection) -> None:
    sample = (FIXTURES / "样例_历史整理版.xlsx").read_bytes()
    save_template(connection, suggest_mapping(read_sample(sample), None))


def test_allowlist_blocks_with_audit_metadata_and_empty_list_blocks_all(
    db_copy: Path,
) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        content = (FIXTURES / "导入_正常批次.xlsx").read_bytes()
        first = poll_mailbox(connection, FakeSource([attachment(content)]), {"other@example.com"})
        assert first.blocked == 1
        row = connection.execute(
            "SELECT status, sender, file_blob, error_code FROM ingest_mail_item"
        ).fetchone()
        assert row == ("blocked", "planner@example.com", None, "sender_not_allowed")

        second = poll_mailbox(
            connection,
            FakeSource([attachment(content + b"different", uid="uid-2")]),
            set(),
        )
        assert second.blocked == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM ingest_mail_item WHERE status = 'blocked'"
            ).fetchone()[0]
            == 2
        )
    finally:
        connection.close()


def test_repeated_poll_is_idempotent(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        register_fixture_template(connection)
        source = FakeSource([attachment((FIXTURES / "导入_正常批次.xlsx").read_bytes())])
        assert poll_mailbox(connection, source, {"planner@example.com"}).new_items == 1
        repeated = poll_mailbox(connection, source, {"planner@example.com"})
        assert repeated.duplicates == 1
        assert connection.execute("SELECT COUNT(*) FROM ingest_mail_item").fetchone()[0] == 1
    finally:
        connection.close()


def test_missing_template_becomes_invalid_file_with_reason(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        connection.execute("DROP TABLE IF EXISTS ingest_template")
        result = poll_mailbox(
            connection,
            FakeSource([attachment((FIXTURES / "导入_正常批次.xlsx").read_bytes())]),
            {"planner@example.com"},
        )
        assert result.invalid_files == 1
        item = list_mail_items(connection)[0]
        assert item.status == "invalid_file"
        assert item.error_code == "template_not_found"
        assert item.error_message == "请先注册 Excel 列映射模板"
    finally:
        connection.close()


def test_pending_confirm_tracks_batch_and_existing_rollback_restores_open_po(
    db_copy: Path,
) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        register_fixture_template(connection)
        count_before = int(connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0])
        result = poll_mailbox(
            connection,
            FakeSource([attachment((FIXTURES / "导入_正常批次.xlsx").read_bytes())]),
            {"planner@example.com"},
        )
        assert result.new_items == 1
        item = list_mail_items(connection)[0]
        assert item.status == "pending_review"
        assert connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before

        batch_id, fresh_report = confirm_mail_item(connection, item.item_id)
        assert fresh_report.valid_count == 8
        assert connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before + 8
        confirmed = get_mail_item(connection, item.item_id)
        assert confirmed.status == "confirmed"
        assert confirmed.batch_id == batch_id

        assert rollback_batch(connection, batch_id) == 8
        assert connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before
    finally:
        connection.close()


def test_confirm_revalidates_and_imports_only_rows_still_valid(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        material_pn = str(
            connection.execute("SELECT material_pn FROM materials LIMIT 1").fetchone()[0]
        )
        supplier_id = str(
            connection.execute("SELECT supplier_id FROM suppliers LIMIT 1").fetchone()[0]
        )
        content = workbook_bytes(
            list(CANONICAL_MAPPING),
            [
                ["PO-MAIL-DRIFT", material_pn, supplier_id, 12, date(2026, 9, 1)],
                ["PO-MAIL-STILL-VALID", material_pn, supplier_id, 13, "2026-09-02"],
            ],
        )
        poll_mailbox(
            connection,
            FakeSource([attachment(content, uid="drift")]),
            {"planner@example.com"},
        )
        item = list_mail_items(connection)[0]
        assert item.valid_count == 2

        connection.execute(
            "INSERT INTO open_po VALUES (?, ?, ?, ?, ?)",
            ["PO-MAIL-DRIFT", material_pn, supplier_id, 1, date(2026, 8, 31)],
        )
        batch_id, fresh_report = confirm_mail_item(connection, item.item_id)
        assert fresh_report.valid_count == 1
        assert [(error.field, error.code) for error in fresh_report.errors] == [
            ("po_id", "already_exists")
        ]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM open_po WHERE po_id IN (?, ?)",
                ["PO-MAIL-DRIFT", "PO-MAIL-STILL-VALID"],
            ).fetchone()[0]
            == 2
        )
        assert rollback_batch(connection, batch_id) == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM open_po WHERE po_id = 'PO-MAIL-STILL-VALID'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_rejected_item_cannot_be_confirmed(db_copy: Path) -> None:
    connection = duckdb.connect(str(db_copy))
    try:
        register_fixture_template(connection)
        poll_mailbox(
            connection,
            FakeSource([attachment((FIXTURES / "导入_正常批次.xlsx").read_bytes())]),
            {"planner@example.com"},
        )
        item_id = list_mail_items(connection)[0].item_id
        reject_mail_item(connection, item_id)
        assert get_mail_item(connection, item_id).status == "rejected"
        with pytest.raises(IngestError) as exc_info:
            confirm_mail_item(connection, item_id)
        assert exc_info.value.code == "mail_item_not_pending"
    finally:
        connection.close()


def test_directory_source_parses_sender_and_moves_to_processed(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = inbox / "a@b.com__订单.xlsx"
    content = workbook_bytes(["po_id"], [["PO-LOCAL"]])
    original.write_bytes(content)

    fetched = DirectoryEmailSource(inbox).fetch_new()
    assert len(fetched) == 1
    assert fetched[0].sender == "a@b.com"
    assert fetched[0].filename == "订单.xlsx"
    assert fetched[0].content == content
    assert not original.exists()
    assert (inbox / "processed" / original.name).read_bytes() == content


def test_mime_parser_returns_only_xlsx_attachments() -> None:
    message = EmailMessage()
    message["From"] = "Planner <planner@example.com>"
    message["Subject"] = "两份订单"
    message["Date"] = "Fri, 17 Jul 2026 09:30:00 +1000"
    message.set_content("attachments")
    message.add_attachment(
        b"first",
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="第一份.xlsx",
    )
    message.add_attachment(
        b"second",
        maintype="application",
        subtype="octet-stream",
        filename="SECOND.XLSX",
    )
    message.add_attachment(
        b"pdf",
        maintype="application",
        subtype="pdf",
        filename="ignore.pdf",
    )

    parsed = parse_mime_attachments(message.as_bytes(), "imap-42")
    assert [item.filename for item in parsed] == ["第一份.xlsx", "SECOND.XLSX"]
    assert [item.content for item in parsed] == [b"first", b"second"]
    assert all(item.sender == "planner@example.com" for item in parsed)
    assert all(item.message_uid == "imap-42" for item in parsed)


def test_lifespan_creates_no_task_when_polling_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INGEST_MAIL_POLL_SECONDS", raising=False)
    from app.main import app, lifespan

    async def exercise_lifespan() -> None:
        async with lifespan(app):
            pass

    with patch("app.main.asyncio.create_task") as create_task:
        asyncio.run(exercise_lifespan())
    create_task.assert_not_called()


def _connection_dependency(db_path: Path):
    def dependency():
        connection = duckdb.connect(str(db_path))
        try:
            yield connection
        finally:
            connection.close()

    return dependency


def test_mail_routes_preserve_manual_gate_and_cover_confirm_and_reject(
    db_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INGEST_MAIL_POLL_SECONDS", "0")
    from app.main import app
    from app.routers.ingest import get_allowed_mail_senders, get_email_source

    connection = duckdb.connect(str(db_copy))
    try:
        save_template(connection, CANONICAL_MAPPING)
        material_pn = str(
            connection.execute("SELECT material_pn FROM materials LIMIT 1").fetchone()[0]
        )
        supplier_id = str(
            connection.execute("SELECT supplier_id FROM suppliers LIMIT 1").fetchone()[0]
        )
        count_before = int(connection.execute("SELECT COUNT(*) FROM open_po").fetchone()[0])
    finally:
        connection.close()

    first = workbook_bytes(
        list(CANONICAL_MAPPING),
        [["PO-MAIL-ROUTE-1", material_pn, supplier_id, 5, "2026-10-01"]],
    )
    second = workbook_bytes(
        list(CANONICAL_MAPPING),
        [["PO-MAIL-ROUTE-2", material_pn, supplier_id, 6, "2026-10-02"]],
    )
    source = FakeSource([attachment(first, uid="route-1"), attachment(second, uid="route-2")])
    dependency = _connection_dependency(db_copy)
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_read_write_db] = dependency
    app.dependency_overrides[get_email_source] = lambda: source
    app.dependency_overrides[get_allowed_mail_senders] = lambda: {"planner@example.com"}
    try:
        with TestClient(app) as client:
            config = client.get("/api/ingest/mail/config")
            assert config.status_code == 200
            assert config.json()["scheduled_poll_enabled"] is False
            assert "password" not in config.json()

            polled = client.post("/api/ingest/mail/poll")
            assert polled.status_code == 200
            assert polled.json()["new_items"] == 2
            with duckdb.connect(str(db_copy), read_only=True) as check:
                assert check.execute("SELECT COUNT(*) FROM open_po").fetchone()[0] == count_before

            items = client.get("/api/ingest/mail/items").json()
            assert len(items) == 2
            assert all(item["status"] == "pending_review" for item in items)
            confirm_item, reject_item = items

            detail = client.get(f"/api/ingest/mail/items/{confirm_item['item_id']}")
            assert detail.status_code == 200
            assert detail.json()["fresh_report"]["valid_count"] == 1

            confirmed = client.post(f"/api/ingest/mail/items/{confirm_item['item_id']}/confirm")
            assert confirmed.status_code == 200
            assert confirmed.json()["row_count"] == 1

            rejected = client.post(f"/api/ingest/mail/items/{reject_item['item_id']}/reject")
            assert rejected.status_code == 200
            assert rejected.json()["status"] == "rejected"
            cannot_confirm = client.post(f"/api/ingest/mail/items/{reject_item['item_id']}/confirm")
            assert cannot_confirm.status_code == 409
            assert cannot_confirm.json()["detail"]["code"] == "mail_item_not_pending"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_read_write_db, None)
        app.dependency_overrides.pop(get_email_source, None)
        app.dependency_overrides.pop(get_allowed_mail_senders, None)
