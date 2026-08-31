"""What this claims, and the controls that make each claim falsifiable."""

import math
import os
import sys
import textwrap
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "fixtures"))

from nondet import check, functions_in, scan  # noqa: E402
from nondet.core import LADDER_VALUES, RUNS  # noqa: E402
from known import LABELS  # noqa: E402

KNOWN = os.path.join(ROOT, "fixtures", "known.py")


def verdict_for(name, path=KNOWN, **kwargs):
    for candidate, arity in functions_in(path):
        if candidate == name:
            return check(path, name, arity, **kwargs)
    raise AssertionError(f"{name} is not in {path}")


def write_module(body):
    d = tempfile.mkdtemp(prefix="nondet-test-")
    path = os.path.join(d, "m.py")
    with open(path, "w") as fh:
        fh.write(textwrap.dedent(body))
    return path


class TheFlagship(unittest.TestCase):
    """One `sorted()` apart, and the whole design turns on separating them."""

    def test_a_set_returned_unsorted_is_caught(self):
        v = verdict_for("dedup_unsorted")
        self.assertEqual(v.state, "nondeterministic")
        self.assertIsNotNone(v.witness)
        self.assertNotEqual(v.witness["a"], v.witness["b"])

    def test_the_sorted_version_is_not_flagged(self):
        # Without this the test above is satisfied by a tool that flags everything.
        self.assertEqual(verdict_for("dedup_sorted").state, "deterministic")

    def test_in_process_repetition_would_have_missed_it(self):
        """THE CONTROL FOR THE ENTIRE DESIGN.

        If calling the function twice in one interpreter caught this, fresh processes
        would be expensive theatre. It does not: string hashing is randomised per
        interpreter, so the answer is stable within a process and different in the next.
        """
        sys.path.insert(0, os.path.join(ROOT, "fixtures"))
        from known import dedup_unsorted

        arg = ["alpha", "beta", "gamma", "delta", "epsilon"]
        in_process = {tuple(dedup_unsorted(arg)) for _ in range(20)}
        self.assertEqual(
            len(in_process), 1,
            "in-process repetition found a difference, so the fresh-process design "
            "is not doing the work this package says it does",
        )
        self.assertEqual(verdict_for("dedup_unsorted").state, "nondeterministic")


class TheEnvironmentIsVaried(unittest.TestCase):
    """Borrowed from `reprotest`: vary the conditions rather than wait for them to vary.

    Hash randomisation comes free with every new process. Timezone and locale do not, so
    a function reading local time looks perfectly stable across three runs on one machine
    and moves the moment it runs anywhere else.
    """

    def test_a_timezone_dependent_function_is_caught(self):
        self.assertEqual(verdict_for("epoch_year").state, "nondeterministic")

    def test_and_it_is_caught_ONLY_because_the_environment_varies(self):
        # THE CONTROL. `fromtimestamp(0).year` does not move with the clock, so three
        # runs under one environment agree every time. If this ever reports
        # nondeterministic, VARIATIONS is not what is doing the work.
        import nondet.core as core

        original = core.VARIATIONS
        core.VARIATIONS = [{"TZ": "UTC", "LC_ALL": "C", "LANG": "C"}]
        try:
            self.assertEqual(
                verdict_for("epoch_year").state, "deterministic",
                "a single fixed environment already caught it, so varying the "
                "environment is not earning its place",
            )
        finally:
            core.VARIATIONS = original


class TheProbeIsWideEnoughToSeeHashOrder(unittest.TestCase):
    """The flagship defect is only detectable if the probe admits many orderings.

    `set_of_keys` was MISSED on roughly one check in thirty-five, and the cause was in
    the ladder rather than the detector: the widest dict it offered had three keys,
    three keys admit 3! = 6 iteration orders, and three fresh processes therefore land
    on the same order by coincidence (1/6)^2 = 2.8% of the time. Measured over 300 hash
    seeds the three-key dict produced exactly those 6 orders and the eight-key dict
    produced 299 distinct ones, so the uniform model above is not an approximation
    anybody has to trust.

    Across a three-version CI matrix that is a failure on about one push in seven, and
    it arrived as a flaky job rather than as a finding, because the test that graded the
    detection asserted the outcome outright.

    So this pins the MECHANISM. An outcome assertion for a probabilistic detector is
    precisely the thing that was already there and did not hold; this one cannot flake
    because it never runs the detector.
    """

    def test_the_ladder_offers_a_dict_wide_enough_that_agreement_is_not_chance(self):
        widest = max((len(v) for v in LADDER_VALUES if isinstance(v, dict)), default=0)
        self.assertGreater(widest, 0, "the ladder offers no dict at all")

        orders = math.factorial(widest)
        # All RUNS processes agreeing by chance, if the orders were equiprobable.
        # They very nearly are: 300 seeds gave 299 distinct orders at widest=8.
        by_chance = (1.0 / orders) ** (RUNS - 1)
        self.assertLess(
            by_chance, 1e-6,
            "the widest dict in the ladder has %d keys, so %d fresh processes agree on "
            "its set order by chance %.3f%% of the time -- which is a MISSED "
            "nondeterministic function, reported as deterministic"
            % (widest, RUNS, 100 * by_chance))

    def test_the_narrow_dict_is_still_there(self):
        # The wide dict was ADDED, not substituted. A small dict is the shape most
        # real callers pass, and dropping it would trade one blind spot for another.
        sizes = sorted(len(v) for v in LADDER_VALUES if isinstance(v, dict))
        self.assertIn(0, sizes, "the empty dict left the ladder")
        self.assertTrue(any(0 < n <= 3 for n in sizes),
                        "the ladder now offers only wide dicts: %r" % (sizes,))


class TheLabelledSet(unittest.TestCase):
    """Graded against labels written down separately, not against function names."""

    def test_detection_and_false_positives(self):
        rows = []
        for name, arity in functions_in(KNOWN):
            if name in LABELS:
                rows.append((name, LABELS[name], check(KNOWN, name, arity).state))
        caught = [n for n, e, g in rows if e == "nondeterministic" and g == e]
        missed = [n for n, e, g in rows if e == "nondeterministic" and g != e]
        false = [n for n, e, g in rows if e == "deterministic" and g == "nondeterministic"]

        self.assertEqual(missed, [], f"missed: {missed}")
        self.assertEqual(false, [], f"false positives: {false}")
        # The denominators, so a fixture file that quietly emptied cannot pass this.
        self.assertGreaterEqual(len(caught), 9)
        self.assertGreaterEqual(len(rows), 18)

    def test_seeded_random_is_not_a_false_positive(self):
        # The specific thing a static gate gets wrong: it greps for `random` and
        # refuses. This is the case a dynamic check exists to get right.
        self.assertEqual(verdict_for("seeded").state, "deterministic")

    def test_importing_time_without_reading_it_is_not_a_finding(self):
        self.assertEqual(verdict_for("duration_arithmetic").state, "deterministic")

    def test_raising_the_same_way_every_time_is_deterministic(self):
        self.assertEqual(verdict_for("always_raises").state, "deterministic")


class CheckerBugsThatOnceLookedLikeFindings(unittest.TestCase):
    """Both of these made real functions unprobeable, and neither was their fault."""

    def test_a_function_that_prints_is_still_probed(self):
        # The vector used to travel on stdout, so a function that printed corrupted it
        # and was reported unprobeable. Four functions in the first real codebase this
        # was pointed at did exactly that, and one of them closed stdout.
        path = write_module(
            """
            import sys
            def chatty(x):
                print("hello from the function under test")
                sys.stdout.flush()
                return str(x).upper()
            """
        )
        v = verdict_for("chatty", path=path)
        self.assertEqual(v.state, "deterministic", v.detail)

    def test_a_function_that_closes_stdout_is_still_probed(self):
        path = write_module(
            """
            import sys
            def rude(x):
                sys.stdout.close()
                return str(x)
            """
        )
        self.assertEqual(verdict_for("rude", path=path).state, "deterministic")

    def test_a_module_using_relative_imports_is_importable(self):
        # Loading by file path makes a package module top-level, so `from .sibling
        # import x` fails. That refused 56 of 68 functions in the first tree this was
        # run against — every refusal a property of the probe, not of the function.
        d = tempfile.mkdtemp(prefix="nondet-pkg-")
        pkg = os.path.join(d, "pkg")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "__init__.py"), "w") as fh:
            fh.write("")
        with open(os.path.join(pkg, "helper.py"), "w") as fh:
            fh.write("def shout(s):\n    return s.upper()\n")
        with open(os.path.join(pkg, "main.py"), "w") as fh:
            fh.write("from .helper import shout\n\n\ndef loud(x):\n    return shout(str(x))\n")
        v = verdict_for("loud", path=os.path.join(pkg, "main.py"))
        self.assertEqual(v.state, "deterministic", v.detail)


class Refusals(unittest.TestCase):
    """A look is never a finding, and it always says why."""

    def test_no_arguments(self):
        path = write_module("def nullary():\n    return 1\n")
        v = verdict_for("nullary", path=path)
        self.assertEqual(v.state, "look")
        self.assertIn("no arguments", v.detail)

    def test_too_many_arguments(self):
        path = write_module("def wide(a, b, c, d):\n    return a\n")
        v = verdict_for("wide", path=path)
        self.assertEqual(v.state, "look")
        self.assertIn("arity 4", v.detail)

    def test_variadic(self):
        path = write_module("def splat(*args):\n    return len(args)\n")
        v = verdict_for("splat", path=path)
        self.assertEqual(v.state, "look")
        self.assertIn("variadic", v.detail)

    def test_a_value_with_no_canonical_form_is_excluded_not_reported(self):
        """An address in a default repr differs every run and means nothing.

        Reporting it would flag every codebase in the world. It is counted instead.
        """
        path = write_module(
            """
            class Thing:
                pass

            def make(x):
                return Thing()
            """
        )
        v = verdict_for("make", path=path)
        self.assertEqual(v.state, "look")
        self.assertGreater(v.unstateable, 0)
        self.assertIn("no canonical form", v.detail)

    def test_a_module_that_cannot_be_imported_says_so(self):
        path = write_module("import a_module_that_does_not_exist\n\ndef f(x):\n    return x\n")
        v = verdict_for("f", path=path)
        self.assertEqual(v.state, "look")
        self.assertIn("could not import", v.detail)


class TheSafetyGate(unittest.TestCase):
    """A determinism checker that leaves files behind is not one you can point at a tree.

    Pointed at a real codebase this found a function whose second run raised
    FileExistsError where the first raised TypeError — a true finding, and proof the
    probe had created a file on somebody's disk.
    """

    def test_a_function_that_writes_a_file_is_refused(self):
        path = write_module(
            """
            def save(x):
                with open("/tmp/nondet-should-never-exist", "w") as fh:
                    fh.write(str(x))
                return x
            """
        )
        v = verdict_for("save", path=path)
        self.assertEqual(v.state, "look")
        self.assertIn("not run", v.detail)
        self.assertFalse(os.path.exists("/tmp/nondet-should-never-exist"))

    def test_a_function_that_shells_out_is_refused(self):
        path = write_module(
            """
            import subprocess
            def sh(x):
                return subprocess.run(["echo", str(x)]).returncode
            """
        )
        self.assertEqual(verdict_for("sh", path=path).state, "look")

    def test_the_gate_does_NOT_refuse_read_only_nondeterminism(self):
        # The gate must not eat the findings. `time`, `random`, `uuid` and set order
        # are read-only and are exactly the target.
        for name in ("stamped", "jittered", "uniquely", "dedup_unsorted", "which_process"):
            self.assertEqual(verdict_for(name).state, "nondeterministic", name)

    def test_unsafe_lifts_the_gate(self):
        path = write_module(
            """
            def save(x):
                with open("/tmp/nondet-unsafe-probe", "w") as fh:
                    fh.write(str(x))
                return str(x)
            """
        )
        self.assertEqual(verdict_for("save", path=path).state, "look")
        v = verdict_for("save", path=path, unsafe=True)
        self.assertEqual(v.state, "deterministic", v.detail)
        try:
            os.remove("/tmp/nondet-unsafe-probe")
        except OSError:
            pass


class TheReportedNumbers(unittest.TestCase):
    def test_the_denominator_travels_with_the_verdict(self):
        v = verdict_for("shout")
        self.assertEqual(v.state, "deterministic")
        self.assertGreater(v.compared, 0)
        self.assertEqual(v.compared + v.unstateable, v.total)
        self.assertIn("absence of a counterexample", str(v))

    def test_more_runs_are_actually_run(self):
        v = verdict_for("shout", runs=5)
        self.assertEqual(v.runs, 5)

    def test_a_fixed_hash_seed_is_reported_not_silently_tolerated(self):
        os.environ["PYTHONHASHSEED"] = "0"
        try:
            v = verdict_for("shout")
            self.assertTrue(v.hash_seed_fixed)
            self.assertIn("PYTHONHASHSEED is FIXED", str(v))
            # ...and the check must still SEE hash-order nondeterminism, because the
            # seed is cleared for the workers rather than inherited.
            self.assertEqual(verdict_for("dedup_unsorted").state, "nondeterministic")
        finally:
            os.environ.pop("PYTHONHASHSEED", None)

    def test_scan_walks_a_tree_and_counts_everything(self):
        census = scan([os.path.join(ROOT, "fixtures")])
        self.assertEqual(census.functions, len(census.verdicts))
        self.assertGreaterEqual(census.functions, 17)


if __name__ == "__main__":
    unittest.main(verbosity=2)
