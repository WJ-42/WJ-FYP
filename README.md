# WJ-FYP

CS3IP final year project: LLM-based multi-agent simulation of a software company.

## Documentation

This repo holds the code only. Design documentation and project history live on Confluence and Jira, not as files in this repo:

- [CS3IP Project Brief](https://finalyearprojectwj.atlassian.net/wiki/spaces/FYP/pages/2293762/CS3IP+Project+Brief), the frozen architecture reference
- [CS3IP Project Diary](https://finalyearprojectwj.atlassian.net/wiki/spaces/FYP/pages/2326529/CS3IP+Project+Diary), the running log of decisions, research, and scope changes
- [Jira board](https://finalyearprojectwj.atlassian.net/jira/software/projects/FYP/boards/1), tracking implementation work

## Structure

- `src/wjfyp/` — orchestration, models, FSM, event log, sandbox, dashboard API, config
- `config/` — preset role team and global settings
- `docker/sandbox.Dockerfile` — base image for per-ticket-attempt sandbox containers; build with `docker build -t wjfyp-sandbox:latest -f docker/sandbox.Dockerfile .` before running real (non-fake) sandbox attempts
- `tests/` — unittest suite (stdlib `unittest`); `test_docker_sandbox_integration.py` self-skips when Docker isn't reachable. Run with `python -m unittest discover -s tests` after installing `pyproject.toml`'s dependencies (including the `api` and `sandbox` extras) into a venv
- `pyproject.toml` — dependencies

## Running the dashboard

`python -m wjfyp.api.server` starts the dashboard at `http://127.0.0.1:8000` (chat feed by default, `#kanban` for the board), reading whichever event log `config/settings.yaml` points at. It's a read-only view over the same SQLite event log the orchestrator writes to; the two are meant to run as separate processes against the same file.
