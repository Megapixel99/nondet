"""Run a function more than once, in FRESH PROCESSES, and see if it answers the same.

WHY FRESH PROCESSES, which is the whole design and not an implementation detail. Calling
a function twice in one interpreter is the check anybody writes first, and it is blind to
the commonest source of nondeterminism in Python:

    $ for i in 1 2 3; do python3 -c "print(list({'alpha','beta','gamma'}))"; done
    ['gamma', 'alpha', 'beta']
    ['beta', 'gamma', 'alpha']
    ['alpha', 'gamma', 'beta']

    $ python3 -c "
    > for i in range(3): print(list({'alpha','beta','gamma'}))"
    ['beta', 'alpha', 'gamma']
    ['beta', 'alpha', 'gamma']
    ['beta', 'alpha', 'gamma']

String hashing is randomised per interpreter, so set and dict iteration order is stable
WITHIN a process and different in every new one. An in-process repeat check reports that
function deterministic every time it is asked, and the build that depends on it breaks on
a machine that started the process differently.

THE VERDICTS ARE ASYMMETRIC, and this is inherited rather than invented. `nondeterministic`
carries a WITNESS — an input and two different answers — and a witness is a fact.
`deterministic` is the absence of one across a finite number of runs over a finite ladder,
which is not the same claim and must never be printed as though it were.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

MAX_ARITY = 3
RUNS = 3

# THE ENVIRONMENT IS VARIED BETWEEN RUNS, not just the interpreter.
#
# Borrowed from `reprotest`, which checks whole builds for reproducibility by varying
# the conditions rather than waiting for them to vary on their own. Hash randomisation
# happens for free with every new process; timezone and locale do not, so a function
# that reads `datetime.now()` in local time or formats against `LC_ALL` looks perfectly
# stable across three runs on one machine and moves the moment it runs anywhere else.
#
# It sharpens the claim as well as the detection: "deterministic" then means "gave the
# same answer under these varied conditions", which is a stronger and more useful thing
# to have been told than "gave the same answer three times in a row here".
VARIATIONS = [
    {"TZ": "UTC", "LC_ALL": "C", "LANG": "C"},
    {"TZ": "Pacific/Kiritimati", "LC_ALL": "C", "LANG": "C"},
    {"TZ": "Etc/GMT+12", "LC_ALL": "C", "LANG": "C"},
]
PER_FUNCTION_SECONDS = 30
REPR_INLINE = 200

# A small deterministic ladder. It is deliberately NOT assay's cross-language document:
# this compares one function with itself in one language, so it can use values that
# document could not carry — and it needs strings whose HASH ORDER can vary, which is the
# defect this tool exists to see.
LADDER_VALUES = [
    0, 1, 2, -1, 7, 255, 3.5, -0.5, True, False, None,
    "", "a", "abc", "Hello, World!", "  padded  ", "10",
    ["alpha", "beta", "gamma", "delta", "epsilon"],
    [1, 2, 3],
    [],
    {"alpha": 1, "beta": 2, "gamma": 3},
    {},
]


def ladder(arity: int) -> list[list]:
    """Argument lists for `arity`. One value per position, then a stride over pairs."""
    if arity == 1:
        return [[v] for v in LADDER_VALUES]
    n = len(LADDER_VALUES)
    rows = []
    for i in range(n):
        rows.append([LADDER_VALUES[(i * (k + 1) + k * 3) % n] for k in range(arity)])
    seen, out = set(), []
    for row in rows:
        key = repr(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


@dataclass
class Verdict:
    ref: str
    state: str                      # "nondeterministic" | "deterministic" | "look"
    detail: str = ""
    witness: dict | None = None
    runs: int = 0
    compared: int = 0               # rungs that every run could state
    total: int = 0                  # rungs walked
    unstateable: int = 0
    hash_seed_fixed: bool = False

    def __str__(self) -> str:
        if self.state == "nondeterministic":
            w = self.witness or {}
            return (
                f"nondeterministic  {self.ref}\n"
                f"         {w.get('args')} -> {w.get('a')}  then  {w.get('b')}\n"
                f"         a witness is a fact: this function gave two answers to one input"
            )
        if self.state == "look":
            return f"look             {self.ref} — {self.detail}"
        note = ""
        if self.hash_seed_fixed:
            note = (
                "\n         PYTHONHASHSEED is FIXED in this environment, so hash-order "
                "nondeterminism\n         could not have been seen — this verdict is "
                "blind to it"
            )
        return (
            f"deterministic    {self.ref} — same answers across {self.runs} fresh "
            f"processes on {self.compared} of {self.total} rungs{note}\n"
            f"         which is the absence of a counterexample, not proof of its absence"
        )


@dataclass
class Census:
    """probed + not_probed == functions, with the reasons named as well as counted."""

    functions: int = 0
    verdicts: list = field(default_factory=list)

    def tally(self) -> dict:
        out: dict[str, int] = {}
        for v in self.verdicts:
            out[v.state] = out.get(v.state, 0) + 1
        return out


# --------------------------------------------------------------------------- #
# Resolving a reference.
# --------------------------------------------------------------------------- #

def functions_in(path: str) -> list[tuple[str, int]]:
    """[(name, arity)] for every module-level function in `path`."""
    with open(path, encoding="utf-8") as fh:
        try:
            tree = ast.parse(fh.read())
        except SyntaxError:
            return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            arity = len(args.posonlyargs) + len(args.args)
            if args.vararg or args.kwarg or args.kwonlyargs:
                arity = -1          # not callable from a fixed ladder
            out.append((node.name, arity))
    return out


def refuse(name: str, arity: int) -> str | None:
    """The FIRST reason this function cannot be probed, or None."""
    if arity < 0:
        return "variadic or keyword-only — the ladder walks fixed argument lists"
    if arity == 0:
        return "no arguments — nothing for a ladder to vary"
    if arity > MAX_ARITY:
        return f"arity {arity} (no ladder above {MAX_ARITY})"
    return None



# --------------------------------------------------------------------------- #
# The SAFETY gate, which is a different question from the determinism one.
# --------------------------------------------------------------------------- #
#
# THIS TOOL EXECUTES THE CODE IT IS ASKED ABOUT. Pointed at a real tree it found a
# function whose second run raised `FileExistsError` where the first raised
# `TypeError` — a true finding, and also proof that the probe had created a file on
# somebody's disk. A determinism checker that leaves artefacts behind is not one you
# can point at a working tree.
#
# So there are TWO gates and they ask different questions:
#
#   is it SAFE to run?          static, conservative, and refusing costs a `look`
#   is it DETERMINISTIC?        dynamic, and the whole point of the package
#
# The line is drawn at WRITING AND COMMUNICATING, not at impurity. `time`, `random`,
# `uuid`, `os.getpid` and set ordering are all read-only and all nondeterministic —
# they are exactly the target, and a gate that refused them would refuse the findings.
# What gets refused is anything that could change the world outside the process.
WRITES_OR_COMMUNICATES = {
    "subprocess", "socket", "shutil", "requests", "urllib", "http", "smtplib",
    "ftplib", "telnetlib", "webbrowser", "ctypes", "multiprocessing", "sqlite3",
    "pickle", "marshal", "tempfile", "pathlib",
}
DANGEROUS_CALLS = {
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint",
}
DANGEROUS_ATTRS = {
    "remove", "unlink", "rmdir", "mkdir", "makedirs", "rename", "replace", "chmod",
    "chown", "symlink", "link", "truncate", "write", "writelines", "system", "popen",
    "execv", "execve", "fork", "kill", "exit", "_exit", "abort", "rmtree", "copy",
    "copyfile", "copytree", "move", "connect", "send", "sendall", "urlopen", "run",
    "call", "check_call", "check_output", "Popen", "setattr", "delattr",
}


def side_effects(path: str, name: str) -> str | None:
    """A reason this function must not be EXECUTED, or None.

    Deliberately coarse and deliberately conservative: a false refusal costs a `look`
    and a false admission costs somebody's files. It reads the whole module, not just
    the function, because a module-level `shutil.rmtree` runs at import.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            target = node
    if target is None:
        return None

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])
                if alias.name.split(".")[0] in WRITES_OR_COMMUNICATES:
                    imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in WRITES_OR_COMMUNICATES:
                for alias in node.names:
                    imported.add(alias.asname or alias.name)

    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in DANGEROUS_CALLS:
                    return f"calls {func.id}() — this tool would have to run it to answer"
                if func.id in imported and func.id in WRITES_OR_COMMUNICATES:
                    return f"calls into {func.id}"
            elif isinstance(func, ast.Attribute):
                if func.attr in DANGEROUS_ATTRS:
                    root = func.value
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    where = root.id if isinstance(root, ast.Name) else "something"
                    return f"calls {where}.{func.attr}() — running it could change the world"
                if isinstance(func.value, ast.Name) and func.value.id in WRITES_OR_COMMUNICATES:
                    return f"calls into {func.value.id}"
    return None


# --------------------------------------------------------------------------- #
# The worker, which runs in a FRESH interpreter every time.
# --------------------------------------------------------------------------- #

WORKER = r'''
import importlib, importlib.util, json, os, sys

def canonical(value, depth=0):
    """A value both runs can be compared on, or None if it cannot be stated.

    THE DEFAULT REPR IS THE TRAP. `<Thing object at 0x10f3c2e50>` contains an address
    that differs every run, so an object with no __repr__ of its own would be reported
    nondeterministic on every codebase in the world. That is technically true and
    useless, so such a value is UNSTATEABLE rather than a finding, and the count of
    them is printed.
    """
    if depth > 6:
        return "..."
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "NaN"
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        parts = [canonical(v, depth + 1) for v in value]
        if any(p is None for p in parts):
            return None
        return "[" + ",".join(parts) + "]"
    if isinstance(value, (set, frozenset)):
        # NOT SORTED. Sorting here would erase the very defect this tool looks for:
        # a function whose answer is a set has an iteration order, and that order is
        # what varies between processes.
        parts = [canonical(v, depth + 1) for v in value]
        if any(p is None for p in parts):
            return None
        return "set[" + ",".join(parts) + "]"
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():          # insertion order, deliberately not sorted
            ck, cv = canonical(k, depth + 1), canonical(v, depth + 1)
            if ck is None or cv is None:
                return None
            parts.append(ck + ":" + cv)
        return "{" + ",".join(parts) + "}"
    if isinstance(value, bytes):
        return "bytes:" + value.hex()
    return None

def outcome(fn, args):
    try:
        value = fn(*args)
    except BaseException as exc:
        # THE TYPE, NOT THE MESSAGE. Messages carry paths, addresses and timings, so
        # comparing them would report every raising function as nondeterministic for a
        # reason that has nothing to do with the function.
        return "E:" + type(exc).__name__
    text = canonical(value)
    if text is None:
        return "X:" + type(value).__name__
    if len(text) > %d:
        import hashlib
        return "V#" + hashlib.sha256(text.encode()).hexdigest()[:16]
    return "V:" + text

def import_target(path):
    """Import the module at `path`, AS PART OF ITS PACKAGE when it is in one.

    `spec_from_file_location` loads a file as a top-level module, so anything doing
    `from .sibling import x` fails with "attempted relative import with no known parent
    package". On a real tree that is not an edge case: it refused 56 of 68 functions in
    the first codebase this was pointed at, and every one of those refusals was a
    property of the probe rather than of the function. The reason a census names its
    refusals instead of only counting them is so that shape is visible.
    """
    import os
    path = os.path.abspath(path)
    directory, filename = os.path.split(path)
    parts = [filename[:-3]]
    while os.path.exists(os.path.join(directory, "__init__.py")):
        directory, tail = os.path.split(directory)
        if not tail:
            break
        parts.insert(0, tail)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    dotted = ".".join(parts)
    if len(parts) > 1:
        return importlib.import_module(dotted)
    spec = importlib.util.spec_from_file_location(dotted, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module

payload = json.load(sys.stdin)

def emit(obj):
    with open(payload["out"], "w") as fh:
        json.dump(obj, fh)

# THE VECTOR DOES NOT TRAVEL ON STDOUT, and that is not fastidiousness. A probed
# function is entitled to print; several in the first real codebase this was pointed at
# do, and one of them closes stdout. Sharing the channel made four functions look
# unprobeable when the defect was entirely in the probe. So the answer goes to a file
# and the function gets a devnull to write into.
_real_stdout = sys.stdout
sys.stdout = open(os.devnull, "w")

try:
    module = import_target(payload["path"])
except BaseException as exc:
    emit({"error": "could not import %%s (%%s: %%s)"
          %% (payload["path"], type(exc).__name__, exc)})
    sys.exit(0)
fn = getattr(module, payload["name"], None)
if fn is None:
    emit({"error": "no function called " + payload["name"]})
    sys.exit(0)
emit({"vector": [outcome(fn, args) for args in payload["inputs"]]})
''' % REPR_INLINE


def _one_run(path: str, name: str, inputs: list[list], env: dict) -> tuple[list | None, str]:
    fd, out_path = tempfile.mkstemp(prefix="nondet-", suffix=".json")
    os.close(fd)
    payload = json.dumps({"path": os.path.abspath(path), "name": name,
                          "inputs": inputs, "out": out_path})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", WORKER],
            input=payload, capture_output=True, text=True,
            timeout=PER_FUNCTION_SECONDS, env=env,
        )
    except subprocess.TimeoutExpired:
        os.unlink(out_path)
        return None, f"did not finish inside {PER_FUNCTION_SECONDS}s"
    except OSError as exc:
        os.unlink(out_path)
        return None, f"could not start a worker ({exc})"
    try:
        with open(out_path, encoding="utf-8") as fh:
            answer = json.load(fh)
    except (OSError, ValueError):
        # The worker's own exit status is only consulted HERE, when there is no answer
        # to read. A function that calls sys.exit or closes stdout can make the exit
        # status meaningless while the vector is perfectly good.
        tail = (proc.stderr.strip().splitlines() or ["silent"])[-1][:120]
        return None, f"the worker exited {proc.returncode} without a vector ({tail})"
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    if "error" in answer:
        return None, answer["error"]
    return answer["vector"], ""


def check(path: str, name: str, arity: int, runs: int = RUNS,
          unsafe: bool = False) -> Verdict:
    """Is `path::name` deterministic across `runs` fresh interpreters?"""
    ref = f"{path}::{name}"
    why = refuse(name, arity)
    if why:
        return Verdict(ref, "look", why)
    if not unsafe:
        why = side_effects(path, name)
        if why:
            return Verdict(ref, "look", f"not run: {why}")

    inputs = ladder(arity)
    env = dict(os.environ)
    # HASH RANDOMISATION IS THE INSTRUMENT, so a fixed seed inherited from the
    # environment blinds this check to the defect it is best at finding. It is removed
    # here and the fact is reported either way rather than silently corrected.
    fixed = "PYTHONHASHSEED" in env
    env.pop("PYTHONHASHSEED", None)

    vectors = []
    for i in range(runs):
        run_env = dict(env, **VARIATIONS[i % len(VARIATIONS)])
        vector, problem = _one_run(path, name, inputs, run_env)
        if vector is None:
            return Verdict(ref, "look", problem)
        vectors.append(vector)

    total = len(inputs)
    unstateable = 0
    comparable = []
    for i in range(total):
        column = [v[i] for v in vectors]
        if any(o.startswith("X:") for o in column):
            unstateable += 1
            continue
        comparable.append(i)

    for i in comparable:
        column = [v[i] for v in vectors]
        first = column[0]
        for other in column[1:]:
            if other != first:
                return Verdict(
                    ref, "nondeterministic",
                    "two runs disagreed",
                    witness={"args": json.dumps(inputs[i], ensure_ascii=False),
                             "a": first, "b": other},
                    runs=runs, compared=len(comparable), total=total,
                    unstateable=unstateable, hash_seed_fixed=fixed,
                )

    if not comparable:
        return Verdict(
            ref, "look",
            f"no rung produced a value this can compare — {unstateable} of {total} were "
            f"objects with no canonical form",
            runs=runs, total=total, unstateable=unstateable,
        )
    return Verdict(ref, "deterministic", "", runs=runs, compared=len(comparable),
                   total=total, unstateable=unstateable, hash_seed_fixed=fixed)


def scan(paths, runs: int = RUNS, unsafe: bool = False) -> Census:
    """Every module-level function under `paths`."""
    census = Census()
    for path in _python_files(paths):
        for name, arity in functions_in(path):
            if name.startswith("_"):
                continue
            census.functions += 1
            census.verdicts.append(check(path, name, arity, runs=runs, unsafe=unsafe))
    return census


def _python_files(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames
                               if d not in {"__pycache__", ".git", ".venv", "venv",
                                            "node_modules", ".tox", "build", "dist"}]
                for f in sorted(filenames):
                    if f.endswith(".py"):
                        out.append(os.path.join(dirpath, f))
        elif p.endswith(".py"):
            out.append(p)
    return out
