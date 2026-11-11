# Changelog

All notable changes to PhantomDep PR Gate, newest first. Versions are
the GitHub tags/releases on this repo; consumers pin a version with
`uses: RakTikoo/phantomdep-pr-gate@v0.2` (or `@v0.1`).

## v0.2 — 2026-11-10

- **Monorepo recursion:** manifests (`package.json`,
  `requirements.txt`, `pyproject.toml`) are discovered at any depth in
  directory and git-ref modes, paired base↔head by relative path, and
  the manifest path is shown in text / JSON / PR-comment output.
- **Real PyPI download counts in `--live` mode** via the pypistats
  `/recent` API (offline mode still reports `unknown` and never
  fabricates a number; any pypistats failure degrades to `unknown`).
  PyPI typo exoneration is now age ≥ 365 days **or** weekly downloads
  ≥ 10,000, symmetric with npm.
- **Own CI:** `.github/workflows/tests.yml` runs the full suite on
  push + pull_request (Python 3.11 / 3.12).
- **Dogfood fixtures** (clean / bad consumer simulations) with
  expected PR comments byte-compared in tests.
- Adversarial-review fix pass (108 tests, up from 74): fixed a silent
  git-mode skip of manifests under non-ASCII directory names, a
  manifest-cap bug that could invent "added" dependencies, identical
  pre-existing invalid manifests failing every PR, and more — each
  with a named regression test. See `ANALYSIS.md`.
- Released with tags `v0.1` (at the v0.1 head, `eceb988`) and `v0.2`
  (at `97009c0`). Live cross-repo proof in
  RakTikoo/phantomdep-dogfood followed on 2026-11-11 (clean PR green,
  typosquat PR blocked).

## v0.1 — 2026-11-10

- First release. Stdlib-only Python CLI + composite GitHub Action that
  audits **only the dependencies a PR adds** in `package.json`,
  `requirements.txt`, and `pyproject.toml` (repo-root manifests):
  existence on npm/PyPI, age, weekly downloads (npm), typo distance
  to ~50 popular names per ecosystem, and whether the dep is actually
  imported.
- One updatable PR comment (marker-matched, one per PR); exit 1 only
  for `NOT-FOUND` or blocking `SUSPECT-TYPO`, exit 2 for tool/input
  errors, warnings never block.
- Typo policy from the pre-release adversarial review: established
  lookalikes (`preact`, `black`, `ms`, …) get the `TYPO-LOOKALIKE`
  warning, never a block — blocking requires strong evidence.
  Non-registry specs (`workspace:`, `file:`, `git+`, …) are
  `SKIPPED-NONREGISTRY`, never flagged.
- 74 offline tests; fixture corpus 15 base/head pairs.
