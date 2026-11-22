"""FastAPI app: `elf serve` runs it. JSON API under /api, the built React app at /.

Slow operations don't block a request: the optimizer runs as a background job (poll
/api/jobs/{id}), and chat answers stream back as server-sent events (tool calls as
they happen, then the answer).
"""

import json
import queue
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from elfantasy.api import service
from elfantasy.api.state import AppState, DatabaseBusy
from elfantasy.config import PROJECT_ROOT
from elfantasy.rules import RULES_2026_27

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class CompareRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=6)


class OptimizeRequest(BaseModel):
    horizon: int = Field(3, ge=1, le=5)
    sell: list[int] = []
    from_scratch: bool = False
    apply_news: bool = False


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


def create_app(settings=None, rules=RULES_2026_27, frontend_dist: Path = FRONTEND_DIST):
    if settings is None:
        from elfantasy.config import settings
    state = AppState(settings, rules)
    app = FastAPI(title="EuroLeague Fantasy assistant", version="0.1.0")
    app.state.elf = state
    # The Vite dev server runs on another port during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DatabaseBusy)
    def _busy(request: Request, exc: DatabaseBusy):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def need_token():
        if state.fantasy is None:
            raise HTTPException(400, "Set ELF_FANTASY_TOKEN in .env to see your team.")

    @app.get("/api/status")
    def get_status():
        with state.session() as ctx:
            return service.status(ctx, state.fantasy is not None, state.anthropic is not None)

    @app.get("/api/team")
    def get_team():
        need_token()
        with state.session() as ctx:
            return service.team(ctx)

    @app.get("/api/players")
    def get_players(search: str | None = None, position: str | None = None,
                    team: str | None = None, max_price: float | None = None,
                    sort: str = "expected", limit: int = 50):  # fmt: skip
        with state.session() as ctx:
            return service.players(ctx, search, position, team, max_price, sort,
                                   min(max(limit, 1), 400))  # fmt: skip

    @app.post("/api/compare")
    def post_compare(body: CompareRequest):
        with state.session() as ctx:
            return service.compare(ctx, body.ids)

    @app.get("/api/news")
    def get_news():
        with state.session() as ctx:
            return service.news(ctx)

    @app.get("/api/review")
    def get_review():
        with state.session() as ctx:
            return service.season_review(ctx)

    @app.get("/api/schedule")
    def get_schedule(round: int | None = None):  # noqa: A002 (query parameter name)
        with state.session() as ctx:
            return service.schedule(ctx, round)

    @app.post("/api/optimize")
    def post_optimize(body: OptimizeRequest):
        def run():
            with state.session() as ctx:
                return service.optimize(ctx, body.horizon, body.sell, body.from_scratch,
                                        body.apply_news)  # fmt: skip

        job = state.submit("optimize", run)
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job.")
        return {"job_id": job.id, "kind": job.kind, "status": job.status,
                "result": job.result, "error": job.error}  # fmt: skip

    @app.post("/api/chat")
    def post_chat(body: ChatRequest):
        if state.anthropic is None:
            raise HTTPException(400, "Set ELF_ANTHROPIC_API_KEY in .env to use the chat.")
        from elfantasy.assistant.chat import Assistant
        from elfantasy.assistant.tools import Tools

        session_id = body.session_id or uuid.uuid4().hex[:12]
        events: queue.Queue = queue.Queue()

        def emit(kind: str, data: dict):
            events.put(f"event: {kind}\ndata: {json.dumps(data)}\n\n")

        def run():
            try:
                with state.session() as ctx:
                    bot = state.chats.get(session_id)
                    if bot is None:
                        bot = Assistant(Tools(ctx, state.anthropic), state.anthropic)
                        state.chats[session_id] = bot
                    bot.tools.ctx = ctx  # this request's connection
                    bot.on_tool = lambda name, args: emit("tool", {"name": name, "input": args})
                    answer = bot.ask(body.message)
                emit("answer", {"text": answer, "session_id": session_id})
            except Exception as e:  # sent to the browser instead of a broken stream
                emit("error", {"detail": str(e)})
            finally:
                events.put(None)

        threading.Thread(target=run, daemon=True).start()

        def stream():
            yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n"
            while (item := events.get()) is not None:
                yield item

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.delete("/api/chat/{session_id}")
    def delete_chat(session_id: str):
        state.chats.pop(session_id, None)
        return {"ok": True}

    # The built frontend (npm run build). Unknown paths fall back to index.html so the
    # React router can handle them.
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = frontend_dist / path
            if path and file.is_file():
                return FileResponse(file)
            return FileResponse(frontend_dist / "index.html")

    return app
