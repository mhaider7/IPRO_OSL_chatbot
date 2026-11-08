import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from api.schemas import ChatRequest, ChatResponse, Conversation, EscalationResponse

logger = logging.getLogger("hawk")
FRONTEND = Path(__file__).resolve().parents[2] / "IIT_Chatbot_UI_Design"

router = APIRouter()


@router.get("/api/health")
def health(req: Request):
    return {"status": "ok"}


@router.post("/api/chat", response_model=ChatResponse)
def chat(req: Request, body: ChatRequest):
    try:
        result = req.app.state.service.chat(body)
        logger.info("chat completed: escalate=%s reason=%s", result.escalate, result.escalation_reason)
        return result
    except Exception:
        logger.warning("chat service unavailable")
        return JSONResponse(status_code=503, content={"detail": "The assistant could not respond. Please try again."})


@router.post("/api/escalate", response_model=EscalationResponse)
def escalate(req: Request, body: Conversation):
    try:
        return req.app.state.service.escalate(body)
    except Exception:
        logger.warning("email draft service unavailable")
        return JSONResponse(status_code=503, content={"detail": "Could not prepare the email draft. Please try again."})


@router.get("/api/info")
def info(req: Request):
    cfg = req.app.state.config
    return {"name": cfg.name, "short_name": cfg.short_name, "email": cfg.email}


@router.post("/api/chat/stream")
def chat_stream(req: Request, body: ChatRequest):
    return StreamingResponse(
        req.app.state.service.chat_stream(body),
        media_type="application/x-ndjson",
    )


@router.get("/", include_in_schema=False)
def demo():
    """Demo host page: a stand-in for the OSL site with the widget on it."""
    return FileResponse(FRONTEND / "demo.html", headers={"Cache-Control": "no-store"})


@router.get("/chat", include_in_schema=False)
def chat_page(req: Request):
    """The chat app. The widget shows it in an iframe as /chat?embed=1."""
    return FileResponse(
        FRONTEND / "chat.html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": req.app.state.frame_ancestors,
        },
    )


@router.get("/embed.js", include_in_schema=False)
def embed_script():
    # no-cache = browsers revalidate on every page load, so updates show up immediately.
    return FileResponse(
        FRONTEND / "embed.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )
