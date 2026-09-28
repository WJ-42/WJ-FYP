"""Starts the dashboard against the same event log run_demo_ticket.py
writes to, so a real demo run can be watched live in the browser -
chat feed, kanban, and (in intervention mode) real Approve/Request
Changes decisions on a paused ticket.

Usage: PYTHONPATH=src python scripts/run_dashboard.py
Then open http://127.0.0.1:8000/
"""

import uvicorn

from wjfyp.api.server import create_app
from wjfyp.eventlog import EventLog

EVENT_LOG_PATH = "data/live-run.db"


def main() -> None:
    event_log = EventLog(EVENT_LOG_PATH)
    uvicorn.run(create_app(event_log), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
