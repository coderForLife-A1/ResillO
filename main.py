from contextlib import asynccontextmanager
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from backend.api import create_router, storage_error_response
from backend.config import settings
from backend.db import Database
from backend.diagnostics import get_diagnostics, setup_logging
from backend.sync_engine import SyncEngine
from backend.triage_engine import TriageEngine
from backend.triage_worker import TriageWorker

setup_logging(settings.log_dir, settings.log_max_bytes, settings.log_backup_count)

db = Database(settings.database_path)
engine = TriageEngine(settings.ollama_url, settings.ollama_model, settings.connect_timeout, settings.read_timeout)
worker = TriageWorker(db, engine)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.initialize()
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()
        db.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization", "X-Resillo-Token"])


@app.exception_handler(sqlite3.Error)
async def db_exception_handler(_: Request, exc: sqlite3.Error):
    return storage_error_response(exc)


async def verify_token(authorization: str | None = Header(default=None), x_resillo_token: str | None = Header(default=None)) -> None:
    token = x_resillo_token or (authorization[7:] if authorization and authorization.startswith("Bearer ") else None)
    if token != settings.api_token:
        raise HTTPException(status_code=401, detail="invalid local API token")


@app.get("/diagnostics", dependencies=[Depends(verify_token)])
async def app_diagnostics() -> dict[str, Any]:
    return get_diagnostics(db, worker, SyncEngine.get_conflict_count())


app.include_router(create_router(db, worker, settings))


@app.get("/", response_class=HTMLResponse)
async def read_index() -> str:
    with open("index.html", encoding="utf-8") as file:
        return file.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
