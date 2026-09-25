from __future__ import annotations

import unittest

from wjfyp.sandbox.pytest_output import parse_pytest_verbose_output


class ParsePytestVerboseOutputTest(unittest.TestCase):
    def test_passed_and_failed_lines(self) -> None:
        output = (
            "tests/test_foo.py::test_bar PASSED                    [ 50%]\n"
            "tests/test_foo.py::test_baz FAILED                    [100%]\n"
        )
        outcomes = parse_pytest_verbose_output(output)
        self.assertEqual(
            outcomes,
            {"tests/test_foo.py::test_bar": True, "tests/test_foo.py::test_baz": False},
        )

    def test_error_and_skipped_count_as_not_passed(self) -> None:
        output = (
            "tests/test_foo.py::test_setup_fails ERROR              [ 33%]\n"
            "tests/test_foo.py::test_skipped SKIPPED (reason)       [ 66%]\n"
        )
        outcomes = parse_pytest_verbose_output(output)
        self.assertFalse(outcomes["tests/test_foo.py::test_setup_fails"])
        self.assertFalse(outcomes["tests/test_foo.py::test_skipped"])

    def test_non_result_lines_are_ignored(self) -> None:
        output = (
            "=========================== test session starts ============================\n"
            "collected 2 items\n"
            "\n"
            "tests/test_foo.py::test_bar PASSED                    [ 50%]\n"
            "\n"
            "============================== 1 passed in 0.01s ==============================\n"
        )
        outcomes = parse_pytest_verbose_output(output)
        self.assertEqual(outcomes, {"tests/test_foo.py::test_bar": True})

    def test_a_node_id_absent_from_output_is_simply_absent(self) -> None:
        outcomes = parse_pytest_verbose_output("")
        self.assertNotIn("tests/test_foo.py::test_never_ran", outcomes)


if __name__ == "__main__":
    unittest.main()
