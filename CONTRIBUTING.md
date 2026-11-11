# Contributing to PhantomDep PR Gate

Thanks for your interest. PhantomDep is a small, stdlib-only tool with
one job — audit the dependencies a PR *adds* — and one rule that
outranks every feature:

## The precision invariant (read this first)

**PhantomDep must never block legitimate work.** A false block on a
real package is the worst bug this project can ship — worse than a
missed typosquat. Everything follows from that:

- **Blocking requires strong evidence.** Exit 1 happens only for
  `NOT-FOUND` or a blocking `SUSPECT-TYPO` (a near-name that is young
  **and** low-download, or not found at all). Established lookalikes
  (`preact`, `black`, `ms`, …) are `TYPO-LOOKALIKE` warnings.
- **Uncertainty degrades, never accuses.** A failed registry lookup is
  `unknown`, not "not found". An unknown import alias for a PyPI
  package is `imported=unknown`, not `UNUSED`. If you are not sure,
  warn or say `unknown` — do not block.
- **Every new check needs fixtures in both directions:** cases that
  must flag *and* realistic cases that must not. A rule without a
  must-not-flag fixture is not ready.

If a change you are proposing would make any currently-passing,
legitimate dependency block, that change needs a very strong reason
and explicit maintainer sign-off — expect the discussion to start
there.

## Ground rules

- **Standard library only.** No new runtime dependencies, ever. If a
  feature seems to need a package, it probably belongs in `--live`
  mode behind a registry API, or it doesn't belong.
- **Tests are offline and deterministic.** `python3 run_tests.py`
  must pass with no network (registry behaviour is faked with
  `tests/fixtures/registry*`). Live-registry parsing is tested with
  mocked `urlopen` against captured-shape payloads.
- **Never fabricate registry data.** If an API provides no number
  (e.g. PyPI's own JSON API has no download counts), report
  `unknown`. Download counts for PyPI in live mode come from
  pypistats only.
- **No behavioural promises the code doesn't keep.** PhantomDep does
  no CVE / malware / install-script analysis (that's Socket / Snyk
  territory) and the docs say so; keep it that way.

## Getting started

```bash
git clone https://github.com/RakTikoo/phantomdep-pr-gate
cd phantomdep-pr-gate
python3 run_tests.py   # 108 tests, offline, ~2s
```

Try it on the fixture corpus:

```bash
python3 phantomdep.py --base-dir fixtures/01-clean-npm/base \
    --head-dir fixtures/01-clean-npm/head \
    --registry-fixtures tests/fixtures/registry
```

## Making a change

1. Open an issue first for anything bigger than a typo fix, so the
   precision trade-offs can be discussed before you write code.
2. Add or update fixtures under `fixtures/` and registry fixtures
   under `tests/fixtures/` for both directions of your change.
3. Run `python3 run_tests.py` — all suites must be green.
4. Update `README.md` / `CHANGELOG.md` if behaviour or verdicts
   changed, and say plainly in the PR what happens to exit codes.

## Good starter contributions

Look for issues labelled
[`good first issue`](https://github.com/RakTikoo/phantomdep-pr-gate/labels/good%20first%20issue)
and
[`help wanted`](https://github.com/RakTikoo/phantomdep-pr-gate/labels/help%20wanted).
Popular-name lists and the PyPI import-alias map
(`PYPI_ALIASES` in `phantomdep.py`) are deliberately data-shaped —
extending them with tests is a great first PR, as long as every
addition is backed by a fixture proving it doesn't create a false
`UNUSED` or a false typo flag.
