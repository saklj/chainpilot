"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import duckdb

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analytics.risk import database_path
from app.routers import chat, diagnose, forecast, ingest, report, risk, whatif
from ingest.mail import (
    allowed_senders_from_env,
    email_source_from_env,
    mail_poll_seconds_from_env,
    poll_mailbox,
)

LOGGER = logging.getLogger(__name__)


def _poll_mail_once() -> None:
    connection = duckdb.connect(str(database_path()))
    try:
        poll_mailbox(connection, email_source_from_env(), allowed_senders_from_env())
    finally:
        connection.close()


async def _mail_poll_loop(seconds: int) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await asyncio.to_thread(_poll_mail_once)
        except Exception as error:
            LOGGER.warning("Scheduled mail poll failed; retrying next interval: %s", error)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start no background task unless a positive mail interval is configured."""
    seconds = mail_poll_seconds_from_env()
    task = asyncio.create_task(_mail_poll_loop(seconds)) if seconds > 0 else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="ChainPilot API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(risk.router)
app.include_router(forecast.router)
app.include_router(chat.router)
app.include_router(report.router)
app.include_router(whatif.router)
app.include_router(diagnose.router)
app.include_router(ingest.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return the API health status."""
    return {
        "status": "ok",
        "service": "chainpilot-api",
        "version": "0.1.0",
    }
