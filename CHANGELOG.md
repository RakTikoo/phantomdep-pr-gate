# Changelog

All notable changes to PhantomDep PR Gate, newest first. Versions are
the GitHub tags/releases on this repo; consumers pin a version with
`uses: RakTikoo/phantomdep-pr-gate@v0.3.1`.

## v0.3.1 — 2026-11-20

- **Packaging:** `pyproject.toml` added — `pip install
  git+https://github.com/RakTikoo/phantomdep-pr-gate` (or `pipx
  install …`) now installs the single module plus a `phantomdep`
  console command; the version is read from `phantomdep.VERSION`, so
  the package can never drift from the tool. Still stdlib only, zero
  dependencies. The README gains a real install section covering the
  Action, pip/pipx, and clone routes (it previously assumed the reader
  already had the file).
- **Pre-commit hook:** `.pre-commit-hooks.yaml` ships a `phantomdep`
  hook that runs at the pre-push stage, auditing `origin/main` → `HEAD`
  in `--live` mode when manifest files are in the pushed range.
  Packaging, hooks and docs only — the detection code is unchanged.
- **Docs:** README test count corrected (120 → 122, as measured by
  `run_tests.py`); README gains the DeepWiki badge and the
  `npx skills add` agent-install line; `CITATION.cff` /
  `codemeta.json` citation metadata added. This release exists so
  the release tag contains the packaging and docs above — the
  v0.3.0 tag predates them.

## v0.3.0 — 2026-11-14

- **requirements.txt `-r` includes are followed** (issue #4) in the
  directory and git-ref modes: an included file's requirements count
  as declared by the including file, resolved relative to it,
  recursively, with cycle protection and a depth cap. Missing,
  unreadable, symlinked (git mode) or tree-escaping includes warn and
  are not followed. `--diff` mode and the plain text parser keep the
  "not followed" warning (no tree to resolve against).
- **package-lock.json is read** (issue #2) in the directory and
  git-ref modes: a dependency entering through a lockfile-only change
  is audited with identical verdicts and exit policy. Only direct
  dependencies are read — the set recorded in the lockfile's root
  `packages[""]` entry — so transitive churn never manufactures
  findings. lockfileVersion 1 is not read (no reliable
  direct/transitive split; a note is printed). `--diff` mode does not
  read lockfiles.
- Fixtures: five new corpus pairs (18–22) covering includes, include
  cycles, lockfile direct adds, a lockfile typosquat block, and
  transitive-only churn; tests 109 → 120.

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
