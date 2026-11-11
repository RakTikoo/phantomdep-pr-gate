# PhantomDep PR Gate 👻

**One job: audit only the dependencies a pull request *adds*.**

For each added dependency in `package.json`, `requirements.txt`, or
`pyproject.toml`, PhantomDep checks:

- **Existence** — is it on npm / PyPI at all? (the slopsquatting signal:
  LLMs invent package names, and attackers register them)
- **Age** — days since first published; `< 30 days` is `NEWBORN`
- **Weekly downloads** — npm downloads API; for PyPI the JSON API provides
  no download counts, so PhantomDep reports **`unknown`** and never
  fabricates a number
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
  followed** (a warning is printed); only the top-level file is audited.
- `pyproject.toml`: `project.dependencies`,
  `project.optional-dependencies`, PEP 735 `dependency-groups`, and
  `[tool.poetry.dependencies]` (+ poetry group/dev dependencies).

**Non-registry specs are skipped, never flagged:** `workspace:`, `file:`,
`link:`, `git+`/`git:`, `http(s):`, `npm:`, `catalog:`, `portal:` specs and
PEP 508 direct references (`name @ url`) get `SKIPPED-NONREGISTRY`, are
listed separately, and never affect the exit code.

## Verdicts & exit policy (exact)

Per added dep, verdicts can combine: `OK`, `NEWBORN`, `LOW-DOWNLOADS`
(npm weekly downloads < 1000, when known), `SUSPECT-TYPO`,
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

For PyPI, weekly downloads are unknown, so age ≥ 365 d alone exonerates.

| Exit | Meaning |
|---|---|
| `1` | any added dep is `NOT-FOUND` **or** blocking `SUSPECT-TYPO` |
| `0` | otherwise — `NEWBORN` / `LOW-DOWNLOADS` / `UNUSED` / `TYPO-LOOKALIKE` are warnings, noted in the output |
| `2` | usage / input error — including a manifest that is present but unparseable or oversized, invalid TOML, or a git failure other than "path absent at ref" (the file is named) |

## GitHub Action

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
python3 run_tests.py   # 74 tests, zero deps, zero network (FakeRegistry;
                       # LiveRegistry parsing is tested with mocked urlopen
                       # against captured-shape registry payloads)
```

## License

MIT — see `LICENSE`.
