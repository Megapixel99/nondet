"""`nondet` — run it twice in fresh processes and see if it answers the same.

    nondet src/                       # every module-level function under a tree
    nondet src/util.py::normalise     # one function
    nondet src/ --runs 5 --json

Exit codes: 0 nothing found · 1 at least one function is nondeterministic · 2 the tool
could not run.

A `look` NEVER fails the run. A check that cries wolf is one nobody runs, and a function
this could not probe is not a finding about that function — it is a gap in the probe,
and it is counted and named rather than hidden.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .core import RUNS, check, functions_in, scan


def _one(ref: str, runs: int, unsafe: bool = False):
    path, _, name = ref.partition("::")
    for candidate, arity in functions_in(path):
        if candidate == name:
            return check(path, name, arity, runs=runs, unsafe=unsafe)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nondet",
        description="Run a function in fresh processes and see if it answers the same.",
    )
    parser.add_argument("paths", nargs="+", help="files, directories, or FILE::NAME")
    parser.add_argument("--runs", type=int, default=RUNS,
                        help=f"fresh interpreters per function (default {RUNS})")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--unsafe", action="store_true",
                        help="run functions the static gate refuses as side-effecting. "
                             "This tool EXECUTES what it probes; the gate is why it does "
                             "not leave files behind on a working tree.")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="only print findings and the census")
    args = parser.parse_args(argv)

    verdicts = []
    for ref in args.paths:
        if "::" in ref:
            verdict = _one(ref, args.runs, args.unsafe)
            if verdict is None:
                sys.stderr.write(f"nondet: {ref} names no module-level function\n")
                return 2
            verdicts.append(verdict)
        else:
            if not os.path.exists(ref):
                sys.stderr.write(f"nondet: {ref} does not exist\n")
                return 2
            verdicts.extend(scan([ref], runs=args.runs, unsafe=args.unsafe).verdicts)

    findings = [v for v in verdicts if v.state == "nondeterministic"]
    looks = [v for v in verdicts if v.state == "look"]
    clean = [v for v in verdicts if v.state == "deterministic"]

    if args.as_json:
        json.dump(
            {"functions": len(verdicts),
             "nondeterministic": len(findings),
             "deterministic": len(clean),
             "look": len(looks),
             "verdicts": [
                 {"ref": v.ref, "state": v.state, "detail": v.detail,
                  "witness": v.witness, "compared": v.compared, "total": v.total,
                  "unstateable": v.unstateable, "hash_seed_fixed": v.hash_seed_fixed}
                 for v in verdicts
             ]},
            sys.stdout, indent=2, sort_keys=True,
        )
        sys.stdout.write("\n")
        return 1 if findings else 0

    if findings:
        print(f"\nFINDINGS — {len(findings)}, each with a witness:")
        for v in findings:
            print("  " + str(v))
    if looks and not args.quiet:
        print(f"\nLOOK — {len(looks)} the probe could not settle. These never fail the run.")
        for v in looks:
            print("  " + str(v))

    # THE DENOMINATOR IS PRINTED, ALWAYS. A run that probed nothing and a run that
    # probed everything and found nothing print the same word otherwise, and they are
    # not the same result.
    print(f"\n{len(verdicts)} functions: {len(clean)} deterministic, "
          f"{len(findings)} nondeterministic, {len(looks)} not probed")
    if not verdicts:
        print("  nothing was probed, so this is not a clean result — it is no result")
    unstateable = sum(v.unstateable for v in verdicts)
    if unstateable:
        print(f"  {unstateable} rung(s) held values with no canonical form and were "
              f"excluded from every comparison")
    if any(v.hash_seed_fixed for v in verdicts):
        print("  PYTHONHASHSEED was set in this environment; it was cleared for the "
              "workers so hash-order\n  nondeterminism could still be seen")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
