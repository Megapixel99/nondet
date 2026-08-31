"""Functions whose determinism is KNOWN, so detection and false positives can be counted.

The labels are in `LABELS` at the bottom, and the suite reads them from there rather
than from the names — a checker graded against its own naming convention grades nothing.
"""

import datetime
import os
import random
import time
import uuid


# ---- nondeterministic, and each for a different reason ---------------------- #

def stamped(x):
    """Reads the clock."""
    return f"{x}@{time.time()}"


def jittered(x):
    """Unseeded random."""
    return random.random() + (x if isinstance(x, (int, float)) and x is not True else 0)


def uniquely(x):
    """A fresh UUID every call."""
    return f"{x}:{uuid.uuid4()}"


def which_process(x):
    """The pid differs per process and is constant within one."""
    return os.getpid()


def dedup_unsorted(x):
    """THE FLAGSHIP. Stable within a process, different in every fresh one.

    An in-process repeat check calls this deterministic every time it is asked.
    """
    if not isinstance(x, list):
        return None
    return list(set(str(v) for v in x))


def today(x):
    """The date, which is stable for a day and then is not."""
    return datetime.datetime.now().isoformat()


def identity_of(x):
    """`id()` of a fresh object: an address, and addresses move."""
    return id(object()) % 1000003


def set_of_keys(x):
    """The same defect arriving through a dict built from a set."""
    if not isinstance(x, dict):
        return None
    return [k for k in set(x.keys())]


# ---- deterministic, including the near-misses ------------------------------ #

def doubled(x):
    if isinstance(x, (int, float)) and x is not True:
        return x * 2
    return None


def shout(x):
    return str(x).upper() + "!"


def dedup_sorted(x):
    """The FIX for `dedup_unsorted`, and the pair is the point.

    Same function, one `sorted()` different, and a checker that cannot separate these
    two is not measuring anything.
    """
    if not isinstance(x, list):
        return None
    return sorted(set(str(v) for v in x))


def seeded(x):
    """Seeded random IS deterministic, and a static gate that greps for `random` says
    otherwise. This is the false positive a dynamic check is supposed to avoid."""
    return random.Random(42).random()


def duration_arithmetic(x):
    """Imports `time` and never reads the clock — a static gate refuses this."""
    span = datetime.timedelta(seconds=90)
    return int(span.total_seconds()) + (1 if x else 0)


def always_raises(x):
    """Raising the same way every time is deterministic."""
    raise ValueError("no")


def fib(x):
    n = x if isinstance(x, int) and x is not True else 5
    n = min(abs(n), 20)
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def round_tripped(x):
    import json
    try:
        return json.loads(json.dumps(x))
    except (TypeError, ValueError):
        return None


def env_read(x):
    """Reads the environment, which does not change between our runs.

    Included deliberately: a static gate refuses `os`, and this is stable in practice.
    Whether that SHOULD be called deterministic is a judgment, and the README says so.
    """
    return os.environ.get("PATH") is not None


LABELS = {
    "stamped": "nondeterministic",
    "jittered": "nondeterministic",
    "uniquely": "nondeterministic",
    "which_process": "nondeterministic",
    "dedup_unsorted": "nondeterministic",
    "today": "nondeterministic",
    "identity_of": "nondeterministic",
    "set_of_keys": "nondeterministic",
    "doubled": "deterministic",
    "shout": "deterministic",
    "dedup_sorted": "deterministic",
    "seeded": "deterministic",
    "duration_arithmetic": "deterministic",
    "always_raises": "deterministic",
    "fib": "deterministic",
    "round_tripped": "deterministic",
    "env_read": "deterministic",
    "epoch_year": "nondeterministic",
}


def epoch_year(x):
    """TZ-dependent and CLOCK-INDEPENDENT, which is the pair that matters.

    `fromtimestamp(0)` is 1970 in UTC and 1969 west of it. Nothing about it moves with
    time, so running it three times in a row on one machine agrees every time — it is
    caught only because the runs vary the timezone. It is the control for `VARIATIONS`:
    without them this function reads as deterministic.
    """
    return datetime.datetime.fromtimestamp(0).year
