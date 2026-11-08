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

# Sites allowed to show the chat page in an iframe (the embeddable widget).
# Default: the local CSS stress-test page (`make stress-test`).
DEFAULT_FRAME_ANCESTORS = "http://localhost:8002,http://127.0.0.1:8002"


def frame_ancestors_csp(raw: str) -> str:
    """Build the CSP header value from a comma-separated list of origins.

    Entries containing spaces, quotes, or semicolons are dropped so the
    variable can't inject other CSP directives.
    """
    sources = [v.strip() for v in raw.split(",")]
    sources = [v for v in sources if v and not any(c in v for c in " ;'\"")]
    return " ".join(["frame-ancestors", "'self'", *sources])


class NoCacheStaticFiles(StaticFiles):
    """Serve frontend files with no-store so edits show up on a normal reload."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


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
    app.state.frame_ancestors = frame_ancestors_csp(
        os.getenv("HAWK_FRAME_ANCESTORS", DEFAULT_FRAME_ANCESTORS)
    )

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
    app.mount("/assets", NoCacheStaticFiles(directory=FRONTEND), name="assets")
    return app


app = create_app()
