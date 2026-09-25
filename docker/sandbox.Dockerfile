# Base image for per-ticket-attempt sandbox containers (see
# cs3ip-sandbox-design memory and src/wjfyp/sandbox/docker_controller.py).
#
# Deps are baked in at BUILD time, not installed at container run time,
# because attempt containers run with network_disabled=True (no network
# egress by design). git is required for git_diff/git_commit; pytest is
# the v1 placeholder test runner until the evaluation harness (KAN-19)
# wires real per-ticket test commands.
#
# The Python/pytest stack here is a placeholder, same status as the
# TBD-* model fields in config/roles.yaml - it reflects what this repo
# itself happens to use, not a decided target-repo language. Revisit
# once "attach to existing repo" is wired to a real external project.
#
# Build once locally before running real (non-fake) sandbox attempts:
#   docker build -t wjfyp-sandbox:latest -f docker/sandbox.Dockerfile .

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest

RUN useradd --create-home --shell /bin/sh sandbox
USER sandbox
WORKDIR /workspace

CMD ["sleep", "infinity"]
