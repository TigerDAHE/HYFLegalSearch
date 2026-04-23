from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.models.schemas import ChatRequest, ChatResponse, DecisionTrace
from app.workflow.graph import agentic_graph

settings = get_settings()
configure_logging()
logger = get_logger("app.main")
app = FastAPI(title="Legal AgenticSearch", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = str(uuid4())[:8]
    start = time.perf_counter()
    logger.info("request_start id=%s method=%s path=%s", request_id, request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.exception("request_error id=%s elapsed_ms=%.2f", request_id, elapsed_ms)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request_end id=%s status=%s elapsed_ms=%.2f",
        request_id,
        response.status_code,
        elapsed_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
def index():
    index_path = frontend_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    logger.info("chat_in question_len=%s model=%s", len(req.question), req.model or settings.llm_model)
    state_input = {
        "question": req.question,
        "model": req.model,
    }

    try:
        result = agentic_graph.invoke(state_input)
    except Exception as exc:
        logger.exception("workflow_invoke_failed")
        raise HTTPException(status_code=500, detail=f"Workflow failed: {exc}") from exc

    logger.info(
        "chat_trace pipeline=%s complexity=%s confidence=%.2f citations=%s",
        result.get("pipeline_id"),
        result.get("complexity_score"),
        float(result.get("confidence_score", 0.0)),
        len(result.get("citations", [])),
    )

    trace = DecisionTrace(
        complexity_score=int(result.get("complexity_score", 3)),
        confidence_score=float(result.get("confidence_score", 0.5)),
        should_search_web=bool(result.get("should_search_web", True)),
        search_reason=str(result.get("search_reason", "")),
        pipeline_id=(int(result.get("pipeline_id")) if result.get("pipeline_id") is not None else None),
        pipeline_reason=(str(result.get("pipeline_reason")) if result.get("pipeline_reason") else None),
    )

    return ChatResponse(
        answer=str(result.get("answer", "")),
        citations=result.get("citations", []),
        disclaimer=str(result.get("disclaimer", "")),
        trace=trace,
        model_used=req.model or settings.llm_model,
    )
