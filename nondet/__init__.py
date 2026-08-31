"""nondet — run it twice in fresh processes and see if it answers the same.

    from nondet import check, scan

`nondeterministic` carries a witness and is a fact. `deterministic` is the absence of
one across a finite number of runs over a finite ladder, and is worth exactly what that
is worth. The two are never printed as though they were the same kind of claim.
"""

from .core import (
    Census,
    Verdict,
    check,
    functions_in,
    ladder,
    scan,
    LADDER_VALUES,
    MAX_ARITY,
    RUNS,
)

__all__ = ["check", "scan", "Verdict", "Census", "functions_in", "ladder",
           "LADDER_VALUES", "MAX_ARITY", "RUNS"]
__version__ = "0.2.0"
