# Base image for per-ticket-attempt sandbox containers (see
# cs3ip-sandbox-design memory and src/wjfyp/sandbox/docker_controller.py).
#
# Deps are baked in at BUILD time, not installed at container run time,
# because attempt containers run with network_disabled=True (no network
# egress by design). git is required for git_diff/git_commit; pytest is
# also what the evaluation harness's run_hidden_tests() shells out to
# for FAIL_TO_PASS/PASS_TO_PASS verification (see src/wjfyp/eval/).
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
# Without this, `git commit` inside the container fails with "Author
# identity unknown" (exit 128) - confirmed by direct reproduction
# 2026-09-25, the first time this image ran against a real Docker
# daemon (Docker access was broken until FYP-22). git_commit()'s own
# lack of exit-code checking meant this failed completely silently:
# no error, no exception, just a stale HEAD sha returned as if the
# commit had succeeded. See the fix in docker_controller.py alongside
# this for the other half.
RUN git config --global user.email "agent@wjfyp.local" \
    && git config --global user.name "WJ-FYP Engineering Agent"
WORKDIR /workspace

CMD ["sleep", "infinity"]
