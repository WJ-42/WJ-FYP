from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from wjfyp.eventlog import EventLog

STATIC_DIR = Path(__file__).parent / "static"


def create_app(event_log: EventLog, poll_interval: float = 0.5) -> FastAPI:
    """The dashboard's backend: a thin read API over EventLog plus a
    websocket that polls it for new rows and pushes them out, per the
    "all views are just renderers over the one event stream" design.
    Polling (not a push hook from the orchestrator) because the
    orchestrator loop and this server are meant to run as separate
    processes against the same SQLite file, not share in-process state.
    """
    connections: set[WebSocket] = set()

    async def broadcast(payload: dict) -> None:
        dead = set()
        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                dead.add(websocket)
        connections.difference_update(dead)

    async def poll_loop() -> None:
        last_rowid = event_log.latest_message_rowid()
        known_status = {t.id: t.status.value for t in event_log.list_tickets()}
        while True:
            await asyncio.sleep(poll_interval)
            for rowid, message in event_log.get_messages_since(last_rowid):
                last_rowid = rowid
                await broadcast({"type": "message", "data": message.model_dump(mode="json")})
            for ticket in event_log.list_tickets():
                if known_status.get(ticket.id) != ticket.status.value:
                    known_status[ticket.id] = ticket.status.value
                    await broadcast({"type": "ticket_update", "data": ticket.model_dump(mode="json")})

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(poll_loop())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan)

    # async def, not def: Starlette runs sync ("def") path operations in
    # a worker-thread pool, but the sqlite3 connection inside event_log
    # was opened on the main thread and (without check_same_thread=False,
    # which would need its own locking to be safe under real concurrency)
    # can only be used there. async routes stay on the same thread as the
    # poll loop, which already talks to event_log directly.
    @app.get("/api/tickets")
    async def list_tickets() -> list[dict]:
        return [t.model_dump(mode="json") for t in event_log.list_tickets()]

    @app.get("/api/tickets/{ticket_id}/messages")
    async def ticket_messages(ticket_id: str) -> list[dict]:
        return [m.model_dump(mode="json") for m in event_log.get_messages_for_ticket(ticket_id)]

    @app.get("/api/messages")
    async def all_messages(limit: int = 200) -> list[dict]:
        return [m.model_dump(mode="json") for m in event_log.get_all_messages(limit)]

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        connections.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            connections.discard(websocket)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def main() -> None:
    import uvicorn

    from wjfyp.config import load_settings

    settings = load_settings()
    event_log = EventLog(settings.event_log.path)
    uvicorn.run(create_app(event_log), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
