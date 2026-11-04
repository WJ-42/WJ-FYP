#!/bin/bash
# Ollama serialises requests, so task 2 waits for the 4B run rather
# than contending with it and distorting both sets of timings.
SCRATCH=/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad
cd /home/waleed42/Projects/WJ-FYP || exit 1
until ! pgrep -f "wjfyp.eval.cli compare" >/dev/null; do sleep 20; done
rm -rf "$SCRATCH/compare-task2"; mkdir -p "$SCRATCH/compare-task2"
date "+%H:%M:%S task2 start" > "$SCRATCH/compare-task2.log"
.venv/bin/python -u -m wjfyp.eval.cli compare sde-write-a-unit-test-for-append_file-function \
  --repo "$HOME/Projects/openhands-target-append-file" \
  --work-dir "$SCRATCH/compare-task2" \
  --roles-a config/roles_groq.yaml --label-a hierarchical \
  --roles-b config/roles_groq_flat.yaml --label-b flat \
  --backend open --base-url http://10.126.129.12:11434/v1 --model qwen3:8b \
  >> "$SCRATCH/compare-task2.log" 2>&1
date "+%H:%M:%S task2 end" >> "$SCRATCH/compare-task2.log"
