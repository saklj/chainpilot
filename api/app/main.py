"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import chat, diagnose, forecast, ingest, report, risk, whatif

app = FastAPI(title="ChainPilot API", version="0.1.0")

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
