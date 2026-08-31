# `nondet`

[![PyPI](https://img.shields.io/pypi/v/nondet?label=PyPI&color=3775A9)](https://pypi.org/project/nondet/)
[![ci](https://github.com/Megapixel99/nondet/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Megapixel99/nondet/actions/workflows/ci.yml)
[![license MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Run a function more than once, **in fresh processes**, and see if it answers the same.

```sh
nondet src/                     # every module-level function under a tree
nondet src/util.py::normalise   # one function
```

```
FINDINGS — 1, each with a witness:
  nondeterministic  src/features.py::resolve_features
         [["alpha","beta","gamma","delta","epsilon"]] -> V:set["epsilon","delta",…]  then  V:set["beta","gamma",…]
         a witness is a fact: this function gave two answers to one input
```

## Why fresh processes, which is the whole design

Calling a function twice in one interpreter is the check anybody writes first, and it is
blind to the commonest source of nondeterminism in Python:

```
$ for i in 1 2 3; do python3 -c "print(list({'alpha','beta','gamma'}))"; done
['gamma', 'alpha', 'beta']
['beta', 'gamma', 'alpha']
['alpha', 'gamma', 'beta']

$ python3 -c "
> for i in range(3): print(list({'alpha','beta','gamma'}))"
['beta', 'alpha', 'gamma']
['beta', 'alpha', 'gamma']       # <- stable, twenty times out of twenty
['beta', 'alpha', 'gamma']
```

String hashing is randomised per interpreter, so set and dict iteration order is stable
**within** a process and different in every new one. An in-process repeat check reports
that function deterministic every time it is asked, and the build that depends on it
breaks on a machine that started the process differently.

That control is a test: `test_in_process_repetition_would_have_missed_it` asserts the
in-process check finds *no* variation over 20 calls and that `nondet` finds it anyway.
If in-process repetition ever catches it, fresh processes are expensive theatre and the
test says so.

## The verdicts are asymmetric

| verdict | means | worth |
|---|---|---|
| `nondeterministic` | an input, and two different answers to it | **a witness, and a witness is a fact** |
| `deterministic` | no run disagreed, across N runs over a finite ladder | the absence of a counterexample, which is not proof of its absence |
| `look` | could not be probed — and why | never a finding, never fails the run |

The `deterministic` line says so in the output rather than letting you read it as a
guarantee.

## What it is measured at

**A labelled fixture set** (`fixtures/known.py`, 18 functions, labels written down
separately from the names so the checker is not graded against its own convention):

| | |
|---|---|
| nondeterministic caught | **9 of 9** |
| deterministic falsely flagged | **0 of 9** |

The pairs are the point. `dedup_unsorted` and `dedup_sorted` are one `sorted()` apart.
`seeded` uses `random.Random(42)` — deterministic, and the specific false positive a
static gate that greps for `random` produces. `duration_arithmetic` imports `time` and
never reads the clock.

**A real tree** — `trainingResearch/tools`, 283 functions, not written with this tool in
mind:

| | with the safety gate | `--unsafe` |
|---|---|---|
| probed | 127 (45%) | 169 (60%) |
| nondeterministic | **2** | **4** |
| not probed | 156 | 114 |

Both findings are genuine: a function returning a `set`, and one whose value differs
across runs. The gate costs recall and the table says so — one of the two findings it
hides (`harness_backlog`, which returns a path under a fresh temp directory) is a true
positive that the gate can no longer see.

## It executes the code it is asked about

This is the sharpest thing to know, and it was found the hard way. Pointed at a real
tree, `nondet` reported a function raising `TypeError` on one run and `FileExistsError`
on the next — a true finding, and proof that the probe had **created a file on
somebody's disk**.

So there are two gates, asking different questions:

- **is it safe to run?** — static, conservative, and refusing costs a `look`
- **is it deterministic?** — dynamic, and the point of the package

The line is drawn at **writing and communicating**, not at impurity. `time`, `random`,
`uuid`, `os.getpid` and set ordering are read-only and nondeterministic — they are
exactly the target, and a gate that refused them would refuse the findings. What gets
refused is anything that could change the world outside the process: `open()`,
`subprocess`, `shutil`, sockets, `os.remove` and friends.

`--unsafe` lifts it. A test asserts the gate does not eat the read-only findings, and
another asserts a file-writing function is refused *and the file does not appear*.

## What it is for

- **What is safe to memoize or cache.** A function that answers differently per process
  is not.
- **Finding hidden global state**, module-level caches, and accidental clock or
  environment dependence.
- **Reproducible builds and artefacts.** A generator returning a set is how a build
  output changes between machines with no source change.
- **Pre-filtering** candidates for snapshot testing or property testing.

## Prior art

Swept on mechanism nouns across **both registries** — the first sweep queried npm hard
and PyPI only by guessing names, which is how it nearly missed the entry below.

On npm, `keywords:purity` returns 26 packages and **every one is static analysis**
(`pure-react-check`, `@efct/efct`, `@tslite/analysis`, `@ogaga/spacta`);
`keywords:nondeterminism` returns one replay recorder.

On PyPI and in the literature, three neighbours are real and none of them is this:

- **[`reprotest`](https://pypi.org/project/reprotest/)** asks the same question at
  **build** granularity: run it twice under deliberately varied conditions and diff the
  output. It is the direct ancestor of this package's `VARIATIONS`, which were added
  after reading it. It cannot tell you *which function* moved.
- **Groce & Holmes, [*Practical Automatic Lightweight Nondeterminism and Flaky Test
  Detection and Debugging for Python*](https://agroce.github.io/qrs20-2.pdf)** (QRS 2020)
  is the academic prior art, at **test** granularity.
- **`pytest-flakefinder`**, **`pytest-randomly`** and **`flaky`** rerun or reorder
  *tests*. `pytest-randomly` surfaces nondeterminism by controlling seeds; none of them
  names a function or produces a witness input.

The gap is the granularity: nothing found points at an arbitrary function, walks a
ladder, and hands back the input that distinguished two runs.

Static and dynamic are complements rather than rivals, and this ships both: the static
gate decides what is *safe to execute*, the dynamic check decides what is *actually
nondeterministic*. A static gate alone refuses `seeded` and `duration_arithmetic`; a
dynamic check alone writes to your disk.

## Limits

- **The ladder is fixed and small.** Arity 1–3, no variadics, no keyword-only. On a real
  tree that is most of the 156 `look`s, and the census names every one.
- **Values with no canonical form are excluded, not reported.** `<Thing object at
  0x10f3c2e50>` differs every run and means nothing; flagging it would flag every
  codebase in the world. The count of excluded rungs is printed.
- **Exception type, not message.** Messages carry paths, addresses and timings.
- **`PYTHONHASHSEED` is cleared for the workers**, so a fixed seed in your environment
  cannot blind the check — and the fact that it was set is reported either way.
- **The environment is varied between runs** — timezone and locale, borrowed from
  `reprotest`. Hash randomisation is free with every new process; these are not. So
  `deterministic` here means *"the same answer under these varied conditions"*, which is
  a stronger claim than three runs on one machine. `epoch_year` in the fixtures is the
  control: `fromtimestamp(0).year` is 1970 in UTC and 1969 west of it, does not move
  with the clock, and is caught **only** because the timezone varies.
- Zero dependencies, Python 3.9+.

## Tests

```sh
python3 -m unittest discover -s tests
```

25 tests. Two of them are regressions for bugs *in this tool* that first looked like
findings about the code under test: loading a package module by file path broke relative
imports and refused 56 of 68 functions, and sending the result vector over stdout meant
any function that printed corrupted it. Both were caught by pointing the tool at a real
codebase and disbelieving the refusal rate.

One of those fixes is worth its own note: it took reach from 2/68 to 60/68 **while
breaking correctness on all 17 fixtures**. A tool watched only by "how many did it
probe" would have scored that as an improvement.
