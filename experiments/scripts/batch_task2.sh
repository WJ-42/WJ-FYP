#!/bin/bash
# Repeats of task 2 on qwen3:8b. Each repeat is a full hierarchical-vs-flat
# comparison. Results append to a single summary as they land, so partial
# results are readable without waiting for the batch to finish.
SCRATCH=/tmp/claude-1000/-home-waleed42/bc950c4a-424b-48e0-99f6-589ce37e029d/scratchpad
N=${1:-22}
cd /home/waleed42/Projects/WJ-FYP || exit 1
export WJFYP_GRADER_PYTHON="$HOME/Projects/WJ-FYP/.grader-venv/bin/python"
SUM="$SCRATCH/task2_batch_summary.txt"
echo "task2 batch of $N started $(date '+%F %H:%M:%S')" > "$SUM"
for i in $(seq 1 "$N"); do
  d="$SCRATCH/task2-batch/run$i"
  rm -rf "$d"; mkdir -p "$d"
  start=$(date +%s)
  .venv/bin/python -u -m wjfyp.eval.cli compare sde-write-a-unit-test-for-append_file-function \
    --repo "$HOME/Projects/openhands-target-append-file" --work-dir "$d" \
    --roles-a config/roles_groq.yaml --label-a hierarchical \
    --roles-b config/roles_groq_flat.yaml --label-b flat \
    --backend open --base-url http://10.126.129.12:11434/v1 --model qwen3:8b \
    > "$d/run.log" 2>&1
  mins=$(( ($(date +%s) - start) / 60 ))
  h=$(grep -A2 '^hierarchical:' "$d/run.log" | grep score | tr -d ' ' | sed 's/score://')
  f=$(grep -A2 '^flat:' "$d/run.log" | grep score | tr -d ' ' | sed 's/score://')
  v=$(grep '^Verdict' "$d/run.log")
  echo "run $i  hierarchical=$h  flat=$f  ${mins}min  | $v" >> "$SUM"
done
echo "batch finished $(date '+%F %H:%M:%S')" >> "$SUM"
