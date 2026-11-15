from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from backend.api import create_router
from backend.config import settings
from backend.db import Database
from backend.triage_engine import TriageEngine
from backend.triage_worker import TriageWorker

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
app.include_router(create_router(db, worker, settings))


@app.get("/", response_class=HTMLResponse)
async def read_index() -> str:
    with open("index.html", encoding="utf-8") as file:
        return file.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
