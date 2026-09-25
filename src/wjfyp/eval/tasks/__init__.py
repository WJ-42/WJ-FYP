from __future__ import annotations

from wjfyp.eval.task import EvalTask

TASKS: dict[str, EvalTask] = {}


def register(task: EvalTask) -> EvalTask:
    """Add a ported task to the registry. Called once per task module at
    import time (see e.g. wjfyp.eval.tasks.ds_janusgraph_exercise),
    so importing this package's submodules populates TASKS as a side
    effect of loading them - returns the task unchanged so the calling
    module can still bind it to its own module-level name.
    """
    if task.task_id in TASKS:
        raise ValueError(f"duplicate task id: {task.task_id!r}")
    TASKS[task.task_id] = task
    return task


# Side-effect imports: each submodule calls register() at import time,
# so importing this package is what actually populates TASKS.
from wjfyp.eval.tasks import sde_fix_factual_mistake  # noqa: E402,F401
from wjfyp.eval.tasks import sde_write_a_unit_test  # noqa: E402,F401
