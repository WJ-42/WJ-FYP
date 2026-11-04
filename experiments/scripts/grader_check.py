"""Does the repaired grader actually score what it should?

Four solutions, four expected scores. A grader that passes everything
is worse than one that passes nothing, so the bad cases matter as much
as the good one.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

os.environ["WJFYP_GRADER_PYTHON"] = str(Path.home() / "Projects/WJ-FYP/.grader-venv/bin/python")
sys.path.insert(0, "src")

from wjfyp.eval.scoring import score_task
from wjfyp.eval.task import GradingContext
from wjfyp.eval.tasks.sde_write_a_unit_test import TASK

TEMPLATE = Path.home() / "Projects/openhands-target-append-file"

GOOD = '''from openhands.runtime.plugins.agent_skills.file_ops.file_ops import append_file


def test_append_file(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("first\\n")
    append_file(str(p), "second")
    assert "second" in p.read_text()
'''

WRONG_ASSERT = GOOD.replace('assert "second" in p.read_text()', 'assert p.read_text() == "nonsense"')

VACUOUS = '''from unittest import mock


def test_append_file():
    with mock.patch("builtins.open", mock.mock_open()):
        assert True
'''

MISNAMED = '''def test_append_file_other():
    assert True
'''

CASES = [
    ("good, really exercises append_file", GOOD, 5),
    ("wrong assertion", WRONG_ASSERT, 2),
    ("vacuous, mocks open", VACUOUS, 2),
    ("misnamed function", MISNAMED, 1),
]

for label, body, expected in CASES:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        subprocess.run(["git", "clone", "-q", str(TEMPLATE), str(repo)], check=True)
        target = repo / "tests/unit/test_agent_skill.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        score = score_task(TASK, GradingContext(ticket_id="CHK-1", repo_path=str(repo)))
        earned = {r.checkpoint_id: r.earned for r in score.checkpoint_results}
        mark = "OK " if score.points_earned == expected else "!! "
        print(f"  {mark}{label:38} {score.points_earned}/{score.points_total}  (expected {expected})")
        for cid, ok in earned.items():
            print(f"        {'pass' if ok else 'FAIL'}  {cid}")
