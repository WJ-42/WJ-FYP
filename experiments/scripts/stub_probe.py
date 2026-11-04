"""Can the import chain be satisfied by stubbing, instead of by
reconstructing a 2024 dependency tree?

file_ops.py itself imports only stdlib. Everything blocking it is in
the PARENT packages' __init__ files, which Python executes on the way
down. Stubbing those out leaves the module under test untouched.
"""
import sys, types, pathlib

REPO = pathlib.Path.home() / "Projects/openhands-target-append-file"
sys.path.insert(0, str(REPO))

# Pre-register empty modules for the parents that pull heavy deps, so
# Python finds them already imported and never runs their __init__.
for name in (
    "openhands.runtime.e2b",
    "openhands.runtime.e2b.sandbox",
    "openhands.runtime.plugins.jupyter",
    "openhands.runtime.plugins.requirement",
    # Pulled in by a SIBLING agent skill, not by file_ops. tree_sitter_languages
    # ships no wheel for Python 3.14 and building it needs a toolchain plus the
    # grammars, so stubbing the branch is cheaper and touches nothing under test.
    "grep_ast",
    "tree_sitter_languages",
    "tree_sitter",
):
    m = types.ModuleType(name)
    m.E2BBox = object
    m.JupyterPlugin = object
    m.JupyterRequirement = object
    m.Plugin = object
    m.PluginRequirement = object
    # Names the sibling skills pull off these modules. A stub has to
    # satisfy `from x import Y`, not merely exist.
    m.TreeContext = object
    m.filename_to_lang = lambda *a, **k: None
    m.get_parser = lambda *a, **k: None
    m.get_language = lambda *a, **k: None
    sys.modules[name] = m

try:
    from openhands.runtime.plugins.agent_skills.file_ops.file_ops import append_file
    print("  stubbing WORKS - append_file imported")
    import tempfile, os
    d = tempfile.mkdtemp(); p = os.path.join(d, "t.txt")
    open(p, "w").write("a\n")
    append_file(p, "b")
    print("  and it runs:", repr(open(p).read()[:40]))
except Exception as exc:
    print(f"  stubbing failed: {type(exc).__name__}: {exc}")
