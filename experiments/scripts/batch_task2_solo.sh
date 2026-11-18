#!/bin/bash
# The single-agent baseline on task 2, the control arm for the team batch
# in data/task2-batch (batch_task2.sh). Same task, repo, roles file,
# backend, endpoint and model as the hierarchical arm, with one agent
# playing every role (see `wjfyp.eval.cli solo`).
#
# Each run is scored twice, under the upstream rubric (the score the CLI
# prints) and under the corrected one (rescore_task2.py, on a clean clone
# of the run's repo, because the upstream grader leaves the test file
# mutated). One line per run lands in the summary as soon as it is scored.
#
#   batch_task2_solo.sh <first> <last>     e.g. 1 3, then 4 8
#
# A run directory that already exists is never overwritten. The script
# stops instead, so a rerun of a repeat is a deliberate choice.
FIRST=${1:-1}
LAST=${2:-3}
cd /home/waleed42/Projects/WJ-FYP || exit 1
export WJFYP_GRADER_PYTHON="$HOME/Projects/WJ-FYP/.grader-venv/bin/python"
OUT=data/task2-solo
SUM="$OUT/summary.txt"
TASK=sde-write-a-unit-test-for-append_file-function
TESTFILE=tests/unit/test_agent_skill.py
mkdir -p "$OUT"
echo "solo runs $FIRST to $LAST started $(date '+%F %H:%M:%S'), qwen3:8b via Ollama, roles_groq.yaml" >> "$SUM"
for i in $(seq "$FIRST" "$LAST"); do
  d="$OUT/run$i"
  if [ -e "$d" ]; then
    echo "run $i  SKIPPED, $d already exists" >> "$SUM"
    exit 1
  fi
  mkdir -p "$d"
  start=$(date +%s)
  .venv/bin/python -u -m wjfyp.eval.cli solo "$TASK" \
    --repo "$HOME/Projects/openhands-target-append-file" --work-dir "$d" \
    --roles config/roles_groq.yaml --label solo \
    --backend open --base-url http://10.126.129.12:11434/v1 --model qwen3:8b \
    > "$d/run.log" 2>&1
  code=$?
  mins=$(( ($(date +%s) - start) / 60 ))
  s=$(grep -A2 '^solo:' "$d/run.log" | grep score | tr -d ' ' | sed 's/score://')
  st=$(grep -A1 '^solo:' "$d/run.log" | grep status | tr -d ' ' | sed 's/status://')
  if [ "$code" -ne 0 ] || [ -z "$s" ]; then
    echo "run $i  FAILED exit=$code  ${mins}min  | see $d/run.log" >> "$SUM"
    exit 1
  fi
  arm="$d/solo"
  # Same artifacts the team batch kept per arm, so the two can be read
  # side by side. See batch_janitor.sh for why written and graded differ.
  # main.py is main as committed, the state the grader started from. The
  # team batch saved the file AFTER grading, which the upstream rubric
  # mutates, so its graded.py is not the same thing.
  br="ticket/$TASK"
  git -C "$arm" show "$br:$TESTFILE" > "$d/test_agent_skill.written.py" 2>/dev/null \
    || echo "# no $br, or no $TESTFILE on it" > "$d/test_agent_skill.written.py"
  git -C "$arm" show "HEAD:$TESTFILE" > "$d/test_agent_skill.main.py" 2>/dev/null
  git -C "$arm" log --oneline main.."$br" > "$d/branch.commits" 2>/dev/null
  git -C "$arm" diff main.."$br" > "$d/branch.diff" 2>/dev/null
  git -C "$arm" branch --contains "$br" 2>/dev/null | grep -qx '[* ] main' \
    && echo merged > "$d/merged" || echo unmerged > "$d/merged"
  both=$(.venv/bin/python experiments/scripts/rescore_task2.py repo "$arm" 2>"$d/rescore.err")
  echo "run $i  solo upstream=${s%%(*} [rescored ${both}]  ${mins}min  status=$st" >> "$SUM"
done
echo "runs $FIRST to $LAST finished $(date '+%F %H:%M:%S')" >> "$SUM"
