"""Read-only weekly-report endpoints."""

import json
from datetime import date
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from agent.report import assemble_report_data
from app.deps import get_db
from app.exports import build_report_workbook
from app.schemas import Report, ReportMeta

router = APIRouter(prefix="/api/report", tags=["report"])
Db = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]


def _report_payload(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "report_date": row[0].isoformat(),
        "content_md": str(row[1]),
        "narrative_fallbacks": json.loads(row[2] or "[]"),
        "created_at": row[3].isoformat(),
    }


@router.get("/latest", response_model=Report)
def latest_report(connection: Db) -> dict[str, Any]:
    row = connection.execute(
        "SELECT report_date, content_md, narrative_fallbacks, created_at "
        "FROM weekly_report ORDER BY report_date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "report_not_found", "message": "No weekly report found"},
        )
    return _report_payload(row)


@router.get("/list", response_model=list[ReportMeta])
def report_list(connection: Db) -> list[dict[str, str]]:
    rows = connection.execute(
        "SELECT report_date, created_at FROM weekly_report ORDER BY report_date DESC"
    ).fetchall()
    return [
        {"report_date": row[0].isoformat(), "created_at": row[1].isoformat()} for row in rows
    ]


@router.get("/{report_date}/xlsx")
def report_workbook(report_date: date, connection: Db) -> Response:
    try:
        data = assemble_report_data(connection, report_date)
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "report_not_found",
                "message": f"No risk snapshot available for {report_date.isoformat()}",
            },
        ) from error
    return Response(
        content=build_report_workbook(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=chainpilot-weekly-{report_date.isoformat()}.xlsx"
            )
        },
    )


@router.get("/{report_date}", response_model=Report)
def report_by_date(report_date: date, connection: Db) -> dict[str, Any]:
    row = connection.execute(
        "SELECT report_date, content_md, narrative_fallbacks, created_at "
        "FROM weekly_report WHERE report_date = ?",
        [report_date],
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "report_not_found",
                "message": f"Weekly report {report_date.isoformat()} not found",
            },
        )
    return _report_payload(row)
