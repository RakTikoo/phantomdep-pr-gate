# PhantomDep PR Gate 👻

**One job: audit only the dependencies a pull request *adds*.**

For each added dependency in `package.json`, `requirements.txt`, or
`pyproject.toml`, PhantomDep checks:

- **Existence** — is it on npm / PyPI at all? (the slopsquatting signal:
  LLMs invent package names, and attackers register them)
- **Age** — days since first published; `< 30 days` is `NEWBORN`
- **Weekly downloads** — npm downloads API; the PyPI JSON API provides
  no download counts, so in **`--live` mode only** PhantomDep fetches
  them from [pypistats](https://pypistats.org/api/) (`/recent` →
  `data.last_week`). Offline / fixture mode reports **`unknown`** for
  PyPI and never fabricates a number, and any pypistats failure in live
  mode degrades to `unknown`
- **Typo distance** — Levenshtein distance 1–2 to a built-in list of ~50
  popular packages per ecosystem, when the added name is not itself that
  popular name. Blocking requires strong evidence (see the typo policy
  under Verdicts): established lookalikes such as `preact`, `ms`, `black`
  get the warning `TYPO-LOOKALIKE`, never a block. Scoped names are only
  compared with scoped names (`@babel/core` does not make `core` popular)
- **Actually imported?** — greps the head tree for `import` / `require` /
  `from … import` of the package (with a small alias map, e.g.
  `scikit-learn` → `sklearn`; an unknown alias for a hyphenated PyPI name
  is reported as `imported=unknown`, never as a false `UNUSED`)

Output is one markdown PR comment (`--comment-out`), plus text or `--json`.

> **Honesty note — the core check is NOT unique.** Existence / typosquat
> vetting of package names already exists: **pkgguard-cli** vets
> LLM-suggested names pre-install across 8 registries, and **Socket**
> analyses newly added PR dependencies (with far deeper behavioural
> analysis). PhantomDep's delta is *only* the PR-diff workflow:
> **diff-scoping** (only what this PR added — version bumps and removals
> are ignored) **+ import-usage + one-comment review artifact**, in a
> zero-dependency, no-token, stdlib-only CLI. If that workflow delta does
> not matter to you, use pkgguard-cli or Socket. See `ANALYSIS.md`.

## What it looks like

A real comment posted by this Action on a real PR — from the live
cross-repo proof in
[RakTikoo/phantomdep-dogfood](https://github.com/RakTikoo/phantomdep-dogfood/pull/2),
where the PR added the typosquat `reqeusts`:

> ## 👻 PhantomDep PR Gate
>
> | Package | Ecosystem | Manifest | Section | Verdict | Age | Weekly downloads | Imported? |
> |---|---|---|---|---|---|---|---|
> | `reqeusts` | pypi | `requirements.txt` | requirements | NOT-FOUND, SUSPECT-TYPO (close to `requests`) | unknown | unknown | True |
>
> **Blocking: `reqeusts` — NOT-FOUND or blocking SUSPECT-TYPO. Do not merge until resolved.**

That PR's check went red (exit 1) and stayed unmergeable until fixed.
The clean counterpart —
[PR #1](https://github.com/RakTikoo/phantomdep-dogfood/pull/1), adding
`lodash` + `requests` — got the same one comment with both deps `OK`
and a green check; a later version-bump commit updated that same
comment in place rather than posting a second one.

## Quick start (offline-testable mode first)

```bash
# Compare two manifest snapshots — no network unless --live is passed
python3 phantomdep.py --base-dir path/to/base --head-dir path/to/head \
    --registry-fixtures tests/fixtures/registry \
    --comment-out comment.md

# Live registry lookups (explicit opt-in)
python3 phantomdep.py --base-dir base --head-dir head --live

# From git refs, or from a saved unified diff
python3 phantomdep.py --base origin/main --head HEAD --live
python3 phantomdep.py --diff-file pr.diff --head-dir . --live
```

Without `--live` and without `--registry-fixtures`, every registry field
is reported as `unknown` — PhantomDep never guesses offline.

## Manifest coverage

- `package.json`: `dependencies`, `devDependencies`, `peerDependencies`,
  `optionalDependencies` (the section is shown in the output; a dep moving
  between sections is not an addition). **`overrides` / pnpm `overrides`
  are out of scope** — they pin versions of transitive deps rather than
  adding direct deps, and are not parsed.
- `requirements.txt`: plain requirement lines. `-r` includes are **not
  followed** (a warning is printed); that file itself is audited.
- `pyproject.toml`: `project.dependencies`,
  `project.optional-dependencies`, PEP 735 `dependency-groups`, and
  `[tool.poetry.dependencies]` (+ poetry group/dev dependencies).

**Monorepo recursion (v0.2):** in `--base-dir`/`--head-dir` and
git-ref modes, manifests are discovered at **any depth** and paired
base↔head by relative path; the manifest path is shown in text, JSON
and the PR comment. A manifest only in head contributes all its deps;
only in base is ignored. Directories never searched: `.git`,
`node_modules` and `__pycache__` at any depth; `.venv`, `venv`, `dist`,
`build`, `.tox`, `.eggs`, `.mypy_cache`, `.pytest_cache` at the top
level only (a nested `packages/build` is a real package and *is*
audited). Symlinked directories are never followed; in git mode,
symlinked manifests are skipped with a note (never parsed). A pure
manifest *move* (same filename, paired base↔head) is not a re-audit.
The audit cap is **100 manifests per comparison**, applied to the
*same* sorted set on both sides so truncation can never manufacture
an "added" dep, and it is reported as a note in text, JSON and the PR
comment (never just stderr). A hard cap of 1000 manifests fails with
exit 2 rather than auditing partially. The existing 200-added-deps
audit cap still applies (`NOT-AUDITED` beyond it, flagged as an
incomplete audit in the comment). Manifests under `fixtures/`,
`examples/`, `docs/` etc. **are** audited like any other — scope the
Action's `paths:` filter if your repo keeps intentionally fake deps
in test fixtures.
`--diff-file` mode already keyed entries by full manifest path; nested
paths there are regression-tested.

**Non-registry specs are skipped, never flagged:** `workspace:`, `file:`,
`link:`, `git+`/`git:`, `http(s):`, `npm:`, `catalog:`, `portal:` specs and
PEP 508 direct references (`name @ url`) get `SKIPPED-NONREGISTRY`, are
listed separately, and never affect the exit code.

## Verdicts & exit policy (exact)

Per added dep, verdicts can combine: `OK`, `NEWBORN`, `LOW-DOWNLOADS`
(weekly downloads < 1000, when known — npm always in live mode, PyPI
in live mode via pypistats), `SUSPECT-TYPO`,
`TYPO-LOOKALIKE`, `NOT-FOUND`, `UNUSED` (added but never imported),
`UNKNOWN-REGISTRY` (lookup failed), `SKIPPED-NONREGISTRY`,
`NOT-AUDITED` (beyond the 200-added-deps-per-run cap).

**Typo policy — blocking requires strong evidence:**

| Registry evidence | Verdict |
|---|---|
| not found | `SUSPECT-TYPO` (blocks; `NOT-FOUND` blocks too) |
| exists, age ≥ 365 d **or** weekly downloads ≥ 10 000 | `TYPO-LOOKALIKE` (warning) |
| exists, young **and** low/unknown downloads | `SUSPECT-TYPO` (blocks) |
| registry unknown | `TYPO-LOOKALIKE` (warning — unknown ≠ not-found) |

For PyPI, weekly downloads are known only in `--live` mode (via
pypistats); offline, age ≥ 365 d alone exonerates. Young + unknown
downloads still blocks, exactly as before.

| Exit | Meaning |
|---|---|
| `1` | any added dep is `NOT-FOUND` **or** blocking `SUSPECT-TYPO` |
| `0` | otherwise — `NEWBORN` / `LOW-DOWNLOADS` / `UNUSED` / `TYPO-LOOKALIKE` are warnings, noted in the output |
| `2` | usage / input error — including a manifest that is present but unparseable or oversized, invalid TOML, or a git failure other than "path absent at ref" (the file is named) |

## GitHub Action

Consumers pin a tagged release. Tags `v0.1` (at commit `eceb988`)
and `v0.2` (at commit `97009c0`, the current release) both exist —
see `RELEASE_NOTES.md` for the completed release record:
`uses: RakTikoo/phantomdep-pr-gate@v0.2`.

See `action.yml` and `examples/github-workflow.yml`: on `pull_request`,
the action runs PhantomDep with `--base`/`--head` SHAs in `--live` mode
and posts-or-updates a single PR comment (marker-matched, via
`actions/github-script`). No token beyond the default `GITHUB_TOKEN`
comment permission is needed, and no package from the PR is ever
installed or executed.

## Safety

- **Static only.** Scanned-repo code is never executed; no package named
  in any manifest is ever installed.
- Manifest, diff, registry-response and source-file sizes are capped.
- Registry JSON is **untrusted data** and parsed defensively (types
  checked, failures degrade to `unknown`, never a crash).
- No auth headers are sent to registries; nothing to redact, nothing
  printed. See `SECURITY.md`.

## Tests

```bash
python3 run_tests.py   # 108 tests, zero deps, zero network (FakeRegistry;
                       # LiveRegistry + pypistats parsing is tested with
                       # mocked urlopen against captured-shape payloads)
```

This repo's own CI (`.github/workflows/tests.yml`) runs the same suite
on push + pull_request on Python 3.11 / 3.12.

## Dogfood (local simulation + live cross-repo proof)

`fixtures/dogfood-clean/` (adds imported `lodash` + `requests` → all
`OK`, exit 0) and `fixtures/dogfood-bad/` (adds `reqeusts` →
`NOT-FOUND` + `SUSPECT-TYPO`, `freshpypi-lib` → `NEWBORN`, `axios` →
`UNUSED`, exit 1) simulate a consumer repo offline with FakeRegistry;
expected comments are saved at `examples/dogfood-clean-comment.md` and
`examples/dogfood-bad-comment.md`, and `examples/dogfood-workflow.yml`
shows a consumer workflow. **Proven live (2026-11-11):** the
published Action has also run as a real cross-repo consumer in
[RakTikoo/phantomdep-dogfood](https://github.com/RakTikoo/phantomdep-dogfood),
whose workflow pins `RakTikoo/phantomdep-pr-gate@v0.2`. Clean PR #1
(lodash + requests) passed green with exactly one PhantomDep comment
(both deps OK); a version-bump commit updated that same comment in
place (still exactly one). Bad PR #2 (the `reqeusts` typosquat) was
blocked as designed — its run is red with exit 1 and one comment
showing NOT-FOUND + SUSPECT-TYPO. Both PRs remain open as evidence.

## License

MIT — see `LICENSE`.


---

**More from this workshop:** [SkillScan](https://github.com/RakTikoo/skillscan) · [KiCad Preflight](https://github.com/RakTikoo/kicad-preflight) · [Expiry Radar](https://github.com/RakTikoo/expiry-radar) · [CI Command Truth](https://github.com/RakTikoo/ci-command-truth) · [Stranger Suite](https://github.com/RakTikoo/stranger-suite) · [Living Contract Diff](https://github.com/RakTikoo/living-contract-diff) · [Handoff Reconciler](https://github.com/RakTikoo/handoff-reconciler)
