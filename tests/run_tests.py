"""
Test runner for zervermanager.py unit tests — v1.2.0 (dev).
Outputs results in a human-readable, colorised format.

Usage:
    python tests/run_tests.py
"""
import unittest
import os
import sys
import time

# Reconfigure stdout to support unicode characters on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Make sure the dev folder (parent of tests/) is on the path
# dev/tests/run_tests.py  →  ROOT = dev/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ANSI colour helpers
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"


def _readable_class(cls_name):
    """'TestBugFixes' -> 'Bug Fixes'"""
    name = cls_name[4:] if cls_name.startswith("Test") else cls_name
    return ''.join(' ' + ch if ch.isupper() else ch for ch in name).strip()


def _readable_method(method_name):
    """'test_something_cool' -> 'Something cool'"""
    name = method_name[5:] if method_name.startswith("test_") else method_name
    return name.replace('_', ' ').capitalize()


class HumanReadableTestResult(unittest.TestResult):
    """Custom TestResult that prints pretty, grouped, human-readable output."""

    def __init__(self, stream):
        super().__init__()
        self.stream = stream
        self.current_class = None
        self.start_time = time.time()

    # ── per-test hooks ─────────────────────────────────────────────────────

    def startTest(self, test):
        super().startTest(test)

        cls = test.__class__.__name__
        if cls != self.current_class:
            self.current_class = cls
            self.stream.write(f"\n{C.CYAN}{C.BOLD}● {_readable_class(cls)}{C.RESET}\n")

        label = _readable_method(test._testMethodName)
        self.stream.write(f"  {C.DIM}{label} ...{C.RESET} ")
        self.stream.flush()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.stream.write(f"{C.GREEN}✓ passed{C.RESET}\n")

    def addError(self, test, err):
        super().addError(test, err)
        self.stream.write(f"{C.RED}✘ error{C.RESET}\n")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.stream.write(f"{C.RED}✘ failed{C.RESET}\n")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.stream.write(f"{C.YELLOW}⚠ skipped ({reason}){C.RESET}\n")

    # ── error detail + summary ─────────────────────────────────────────────

    def _print_detail(self, flavour, cases):
        for test, traceback in cases:
            self.stream.write(
                f"\n{C.RED}{C.BOLD}{'─' * 60}\n"
                f"{flavour}: {test}{C.RESET}\n"
                f"{traceback}\n"
            )

    def print_summary(self):
        duration = time.time() - self.start_time
        passed   = self.testsRun - len(self.failures) - len(self.errors)
        failed   = len(self.failures)
        errors   = len(self.errors)

        # Print failure/error detail blocks
        self._print_detail("FAIL",  self.failures)
        self._print_detail("ERROR", self.errors)

        # Summary box
        self.stream.write(f"\n{C.BOLD}{'─' * 35}{C.RESET}\n")
        self.stream.write(f"{C.BOLD} Test Summary{C.RESET}\n")
        self.stream.write(f"{'─' * 35}\n")
        self.stream.write(f"  Total   : {self.testsRun}\n")
        self.stream.write(f"  Passed  : {C.GREEN}{passed}{C.RESET}\n")
        if failed:
            self.stream.write(f"  Failed  : {C.RED}{failed}{C.RESET}\n")
        if errors:
            self.stream.write(f"  Errors  : {C.RED}{errors}{C.RESET}\n")
        self.stream.write(f"  Time    : {duration:.2f}s\n")
        self.stream.write(f"{'─' * 35}\n")

        if self.wasSuccessful():
            self.stream.write(f"\n{C.GREEN}{C.BOLD}  ✓ All tests passed!{C.RESET}\n\n")
        else:
            self.stream.write(f"\n{C.RED}{C.BOLD}  ✘ Some tests failed.{C.RESET}\n\n")


class HumanReadableTestRunner:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def run(self, suite):
        result = HumanReadableTestResult(self.stream)
        suite(result)
        result.print_summary()
        return result


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.discover(start_dir=os.path.join(ROOT, "tests"), pattern="test_*.py")

    sys.stdout.write(f"\n{C.BOLD}  Zervermanager Test Suite — v1.2.0 (dev){C.RESET}\n")
    sys.stdout.write(f"{C.DIM}  ================================================={C.RESET}\n")

    runner = HumanReadableTestRunner()
    result = runner.run(suite)

    sys.exit(0 if result.wasSuccessful() else 1)
