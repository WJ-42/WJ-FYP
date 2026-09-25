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

    def test_ansi_colour_codes_around_the_outcome_word_do_not_break_the_match(self) -> None:
        # Captured verbatim from a real `pytest -v` run piped through
        # subprocess.run(capture_output=True) - NOT a hand-written guess.
        # pytest 9.1.1 emits colour codes even when stdout isn't a real
        # terminal, so --color=no alone can't be trusted; this is the
        # belt-and-suspenders strip that covers it either way.
        output = (
            "tests/test_sample.py::test_passes \x1b[32mPASSED\x1b[0m\x1b[32m [ 50%]\x1b[0m\n"
            "tests/test_sample.py::test_fails \x1b[31mFAILED\x1b[0m\x1b[31m [100%]\x1b[0m\n"
        )
        outcomes = parse_pytest_verbose_output(output)
        self.assertEqual(
            outcomes,
            {"tests/test_sample.py::test_passes": True, "tests/test_sample.py::test_fails": False},
        )


if __name__ == "__main__":
    unittest.main()
