# WJ-FYP

CS3IP final year project: LLM-based multi-agent simulation of a software company.

## Documentation

This repo holds the code only. Design documentation and project history live on Confluence and Jira, not as files in this repo:

- [CS3IP Project Brief](https://finalyearprojectwj.atlassian.net/wiki/spaces/FYP/pages/2293762/CS3IP+Project+Brief), the frozen architecture reference
- [CS3IP Project Diary](https://finalyearprojectwj.atlassian.net/wiki/spaces/FYP/pages/2326529/CS3IP+Project+Diary), the running log of decisions, research, and scope changes
- [Jira board](https://finalyearprojectwj.atlassian.net/jira/software/projects/FYP/boards/1), tracking implementation work

## Structure

- `src/wjfyp/models/` — the core Pydantic data models (`Message`, `Ticket`, `Channel`, `RoleConfig`)
- `src/wjfyp/orchestrator/` — the ticket FSM (`fsm.py`), the `Agent` protocol, and the `step`/`run`/`resume` control loop (`loop.py`) that drives a ticket through it. `driver.py` picks up a human's recorded intervention decision and applies it via `resume()`, the piece a long-running orchestrator process would call periodically once one exists
- `src/wjfyp/sandbox/` — the fixed tool interface agents use instead of raw shell access. `docker_controller.py` is the real Docker-backed implementation (one container per ticket-attempt), `fake.py` is the in-memory stand-in the test suite runs against, `git_workspace.py` is the persistent host-side git state beneath the ephemeral containers
- `src/wjfyp/eval/` — the evaluation harness: `task.py`/`scoring.py` for TheAgentCompany-style checkpoint scoring, `mast.py` for MAST failure tagging, `baseline.py` for the single-agent baseline comparison, `runner.py` for tying a task through the orchestrator end to end, `tasks/` for the ported real TheAgentCompany tasks, and `cli.py` for `python -m wjfyp.eval.cli list`/`show`
- `src/wjfyp/eventlog.py` — the SQLite-backed event log: the append-only message stream, ticket-state projection, custom role store, and intervention decision recording, all in one file since they're one shared datastore
- `src/wjfyp/api/` — the dashboard: `server.py` is the FastAPI backend, `static/index.html` is the single-page frontend (chat feed, kanban, roles editor), no build step
- `config/` — preset role team and global settings
- `docker/sandbox.Dockerfile` — base image for per-ticket-attempt sandbox containers; build with `docker build -t wjfyp-sandbox:latest -f docker/sandbox.Dockerfile .` before running real (non-fake) sandbox attempts
- `tests/` — unittest suite (stdlib `unittest`). `test_docker_sandbox_integration.py` self-skips when the Docker daemon isn't reachable or the image isn't built, rather than failing the whole suite. Run with `python -m unittest discover -s tests` after installing `pyproject.toml`'s dependencies (including the `api` and `sandbox` extras) into a venv
- `pyproject.toml` — dependencies

## Running the dashboard

`python -m wjfyp.api.server` starts the dashboard at `http://127.0.0.1:8000`, reading whichever event log `config/settings.yaml` points at. It's a thin API over the same SQLite event log the orchestrator writes to, meant to run as a separate process against the same file, not an in-process view.

Three tabs: chat feed (default), kanban (`#kanban`), and roles (`#roles`). `#ticket:<id>` opens straight to one ticket's thread. Most of the API is read-only, but two things write: `POST`/`PUT`/`DELETE /api/roles` manage custom roles (presets from `config/roles.yaml` can't be edited or deleted), and `POST /api/tickets/{id}/decision` records a human's response to a ticket paused for intervention-mode approval. Recording a decision doesn't apply it by itself: that's `wjfyp.orchestrator.driver.apply_pending_decisions()`, meant to be called periodically by whatever process is actually driving the orchestrator.

## Running the evaluation harness

`python -m wjfyp.eval.cli list` shows every ported benchmark task; `show <task_id>` shows one task's full prompt and checkpoints. `wjfyp.eval.runner.run_eval_task()` is the function that actually runs a task through the orchestrator, either the real multi-agent team or `wjfyp.eval.baseline.single_agent_pool()`'s baseline, and scores and MAST-tags the result. No real LLM-backed `Agent` implementation exists yet, so this is exercised in tests against the same scripted stand-ins used throughout the rest of the suite, ready to use once a real one does.

## Docker

Building the sandbox image (see Structure above) and running the full test suite for real both need a working Docker daemon connection. If `docker ps` reports a permission error even after being added to the `docker` group, a fresh login session usually fixes it: group membership changes don't apply to already-running shells, so `newgrp docker` or a new terminal session is often enough without touching the daemon itself.
