"""Lets the task's test file import the module it is testing.

openhands/runtime/__init__.py and plugins/__init__.py import packages
whose 2024 APIs no longer exist (e2b.sandbox.exception) or which ship
no wheel for Python 3.14 (tree_sitter_languages). Python executes
those on the way down to file_ops, so the test cannot import its own
subject, which is why the repo's pytest.ini wrote the whole thing off
as an accepted limitation.

file_ops.py imports only stdlib. Nothing blocking it is anything it
uses. So the blocking parents are replaced with empty modules before
collection, and the module under test is left exactly as it is.

This is deliberately NOT a stub of file_ops or append_file. Stubbing
the thing being tested would make every test pass and the checkpoint
meaningless.
"""
import sys
import types

_STUBS = {
    "openhands.runtime.e2b": {},
    "openhands.runtime.e2b.sandbox": {"E2BBox": object},
    "openhands.runtime.plugins.jupyter": {
        "JupyterPlugin": object,
        "JupyterRequirement": object,
    },
    "openhands.runtime.plugins.requirement": {
        "Plugin": object,
        "PluginRequirement": object,
    },
    "grep_ast": {
        "TreeContext": object,
        "filename_to_lang": lambda *a, **k: None,
    },
    "tree_sitter": {"Language": object, "Parser": object},
    "tree_sitter_languages": {
        "get_language": lambda *a, **k: None,
        "get_parser": lambda *a, **k: None,
    },
}

for name, attrs in _STUBS.items():
    module = types.ModuleType(name)
    for attr, value in attrs.items():
        setattr(module, attr, value)
    sys.modules.setdefault(name, module)
