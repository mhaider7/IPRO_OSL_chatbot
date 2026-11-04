import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import FRONTEND, router
from api.service import RagService
from config.loader import load_config, load_docs
from retrieval.chroma_setup import build_collection

logger = logging.getLogger("hawk")


def create_app() -> FastAPI:
    config = load_config()
    logger.info("Loading: %s", config.name)

    chunks = load_docs(config.docs_path)
    logger.info("Ingested %d chunks from %s", len(chunks), config.docs_path)

    collection = build_collection(chunks)
    service = RagService(config, collection)

    app = FastAPI(
        title=f"{config.name} Chatbot API",
        version="0.2.0",
        description=f"RAG chatbot for {config.name}.",
    )
    app.state.config = config
    app.state.service = service

    origins = [
        v.strip()
        for v in os.getenv("HAWK_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
        if v.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request. Check the message length, required fields, and email address."},
        )

    app.include_router(router)
    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")
    return app


app = create_app()
