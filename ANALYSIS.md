# PhantomDep PR Gate — Analysis

*Built 2026-11-10. Local only; nothing pushed or published.*

## Honesty statement (required)

**The core existence check in this tool is NOT unique.** pkgguard-cli
already vets LLM-suggested package names for existence and typosquatting
across 8 registries, and Socket already analyses dependencies added in a
pull request. PhantomDep's delta is **only** the PR-diff workflow:
diff-scoping (audit strictly what this PR added; version bumps and
removals ignored) + import-usage checking + a single updatable PR-comment
review artifact, in a zero-dependency stdlib-only CLI with no account or
token. Nothing below should be read as claiming the core check is new.

## What was built

`phantomdep.py` (stdlib-only, deterministic given the same registry data):

- **Input modes:** (a) `--base <ref> --head <ref>` via `git show`,
  (b) `--diff-file patch.diff` (unified-diff heuristic parser, incl.
  single-line `package.json` whole-line replacement), (c) `--base-dir` /
  `--head-dir` manifest-snapshot comparison (primary, offline-testable).
- **Manifests:** `package.json` (`dependencies`, `devDependencies`,
  `peerDependencies`, `optionalDependencies` — sections tracked and shown),
  `requirements.txt` (`-r` includes warned, not followed), `pyproject.toml`
  (`project.dependencies` + `project.optional-dependencies`, PEP 735
  `dependency-groups`, poetry dependencies; `tomllib` with a
  dependencies-array-scoped fallback for Python < 3.11; invalid TOML is an
  exit-2 input error). Non-registry specs (`workspace:`/`file:`/`git+`/
  `npm:`/… and PEP 508 direct references) are `SKIPPED-NONREGISTRY`.
- **Checks per added dep:** existence, age in days (npm `time.created` /
  earliest PyPI release `upload_time`), weekly downloads (npm API; PyPI
  JSON API has none → honestly `unknown`), typo distance (stdlib
  Levenshtein, distance 1–2 vs ~50 popular names per ecosystem, added name
  itself excluded), import usage (grep of head tree, `node_modules`/`.git`
  excluded, PyPI alias map e.g. `scikit-learn`→`sklearn`,
  `pillow`→`PIL`; unknown hyphenated-PyPI alias → `imported=unknown`).
- **Verdicts:** `OK` / `NEWBORN` (<30 d) / `LOW-DOWNLOADS` (<1000/wk,
  when known) / `SUSPECT-TYPO` / `TYPO-LOOKALIKE` / `NOT-FOUND` / `UNUSED` /
  `SKIPPED-NONREGISTRY` / `NOT-AUDITED`, combinable.
- **Exit policy (exact, v0.2):** `1` iff any `NOT-FOUND` or *blocking*
  `SUSPECT-TYPO` (see the typo policy in Adversarial review / README);
  `0` otherwise (warnings noted); `2` usage/input error, including
  present-but-unparseable manifests and non-absence git failures.
- **Registry:** `FakeRegistry` (fixture-file backed) injected everywhere
  in tests; `LiveRegistry` (urllib, 10 s timeout, 2 MB response cap)
  only under explicit `--live`. Lookup failures degrade to `unknown`.

## Corpus score

Fixture corpus first (`fixtures/`, 11 base/head pairs), fixed reference
date 2026-11-10, mocked registry (`tests/fixtures/registry/*.json`):

| Pair | Planted case | Expected | Got |
|---|---|---|---|
| 01-clean-npm | adds imported `lodash` | OK | ✅ OK |
| 02-typo-pypi | adds `reqeusts` | NOT-FOUND + SUSPECT-TYPO (of `requests`), exit 1 | ✅ |
| 03-newborn-pypi | adds 3-day-old `freshpypi-lib` | NEWBORN | ✅ |
| 04-unused-npm | adds `axios`, never imported | UNUSED | ✅ |
| 05-scoped-npm | adds `@babel/core`, required | OK (scoped handling) | ✅ |
| 06-version-bump | `lodash` version bump only | ignored (0 additions) | ✅ |
| 07-removed | dep removed only | ignored (0 additions) | ✅ |
| 08-devdeps-npm | adds `eslint` in devDependencies | detected, OK | ✅ |
| 09-pyproject-alias | adds `scikit-learn` (imported as `sklearn`) + `requests` via optional-dependencies | both OK | ✅ |
| 10-requirements-clean | adds imported `requests` | OK, downloads `unknown` | ✅ |
| 11-newborn-npm | adds 3-day-old `tiny-new-lib`, 12 weekly downloads | NEWBORN + LOW-DOWNLOADS | ✅ |

| 12-typo-lookalike-npm | adds imported `preact` (distance 1 from `react`) | TYPO-LOOKALIKE warning, exit 0 | ✅ |
| 13-peer-move-npm | `lodash` moves peerDependencies → dependencies | ignored (0 additions) | ✅ |
| 14-nonregistry-npm | adds `local-lib: workspace:*` | SKIPPED-NONREGISTRY, exit 0 | ✅ |
| 15-scoped-clean-npm | adds `@angular/core`, imported | OK (no scoped typo false-positive) | ✅ |

**Score: 10/10 original added deps verdicted as planted; 2/2 original
non-addition pairs correctly produced zero additions; 4/4 new regression
pairs as expected. 74/74 tests pass**
(`python3 run_tests.py`, offline, `FakeRegistry` only — includes CLI
exit-code, `--json`, `--comment-out`, diff-file, git-mode, mocked-urlopen
registry-parsing and unit tests).

One real bug was found by end-to-end (non-unit) checking and fixed:
`--diff-file` originally missed deps in single-line `package.json`
files, where git emits a whole-line replacement instead of a per-key
line. The parser now handles whole-object JSON diff lines; a regression
test covers it.

## Real-world check

- **Synthetic PR from local trees:** SkillScan and KiCad Preflight are
  stdlib-only and ship **no** `pyproject.toml` / `requirements.txt` /
  `package.json` (verified by filesystem search) — their real state is
  "no manifest". A synthetic PR copy adding `requests==2.32.3` plus an
  importing module was audited in `--live` mode: `requests` → `OK`,
  age 5748 days, downloads `unknown` (honest), imported `True`, exit 0.
  The generated comment is saved at `examples/sample-comment.md`.
- **Git modes verified end-to-end** on a throwaway local repo:
  `--base HEAD~1 --head HEAD` and `--diff-file` both found the added
  `lodash` and verdicted `OK` against fixtures.
- **Live mode (run once, 2026-11-10, network available):** `requests`
  (PyPI) → exists, age 5748 d, weekly downloads `unknown` as designed;
  `lodash` (npm) → exists, age 5314 d, weekly downloads as returned by
  the npm downloads API at run time. Live client response shapes match
  the parser (npm `time.created`, PyPI earliest release
  `upload_time_iso_8601`). No crash, exit 0. Had the network been
  blocked, lookups would have degraded to `UNKNOWN-REGISTRY` (exit 0)
  by design.

## Incumbent re-verification (2026-11-10, web search)

- **pkgguard-cli** (PyPI, also listed on Socket's directory): "Vet the
  packages and repos your AI assistant recommended — before you install
  them." Checks existence + typosquat/homoglyph (1–2 edits) across PyPI,
  npm, crates.io, Go, RubyGems, Packagist, NuGet, pub.dev; no API key;
  stdlib-only core. It is a *pre-install name list* tool, not a PR-diff
  tool — but it fully owns the core check. Adjacent newcomers found in
  the same search (`pkg-guard`, `slopsquat-guard`) confirm the niche is
  crowding fast.
- **Socket** (socket.dev, "Guide to Socket for GitHub"): watches
  manifest files; when a PR adds a dependency it analyses behaviour and
  comments on the PR; detects typosquats, install scripts, telemetry,
  native code, known malware, and more. This is the closest incumbent —
  it already does PR-diff scoping *and* a PR comment, with behavioural
  depth PhantomDep deliberately does not attempt (no CVE/malware
  analysis). PhantomDep's remaining delta vs Socket: fully local /
  offline-capable, zero account, deterministic fixture-testable output,
  and the import-usage (`UNUSED`) signal.
- **Conclusion:** the flagged status from the research report stands.
  This build is a workflow/differentiation experiment, not a novel check.

## Limitations

- Only npm + PyPI; only three manifest filenames at repo root (no
  monorepo/workspace recursion, no lockfiles, no transitive deps).
- `--diff-file` parsing is heuristic: unusual diff formats, renamed
  manifests, or `package.json` edits expressed as context-heavy hunks
  can be missed. `--base-dir/--head-dir` and git-ref modes are exact.
- Import-usage is a grep, not an AST: dynamic imports with variables,
  re-exports through barrels, plugin-loaded deps, CLI-only devDeps and
  type-only packages can read as `UNUSED` (advisory only, never blocks).
- Typo detection uses a fixed ~50-name popular list per ecosystem;
  distance is plain Levenshtein (no keyboard/homoglyph weighting).
- Age uses first-publish time, which registries can make misleading
  (name re-registration, transferred ownership are not detected).
- PyPI download counts are `unknown` in every mode in this build
  (pypistats integration was deliberately not faked or bundled).
- Git-ref mode reads manifests at repo root via `git show` and scans
  the `--repo` working tree for imports, which assumes the head ref is
  checked out.

## Expansion ladder

1. More manifests/ecosystems: lockfiles, monorepo recursion, crates.io,
   Go modules (the pkgguard breadth play — only if the workflow delta
   proves wanted).
2. pypistats-backed PyPI download counts in `--live` mode.
3. Org policy mode: configurable thresholds, allow/block lists, SARIF
   output for code-scanning dashboards.
4. Hosted registry-watch for your own package names (squat early-warning).
5. Behavioural signals are **not** on the ladder — that is Socket/Snyk
   territory; PhantomDep should stay the fast, boring, local gate.

## GitHub integration story

`action.yml` (composite) + `examples/github-workflow.yml`: triggers on
`pull_request` touching a manifest, runs PhantomDep with the PR's base
and head SHAs in `--live` mode, writes `phantomdep-comment.md`, then a
`actions/github-script` step posts the comment or updates the existing
one (matched by the hidden marker `<!-- phantomdep-pr-gate -->` anywhere
in the body, looked up with pagination — exactly one comment per PR),
and a final Enforce step (both with `if: always()`) fails on ANY
PhantomDep exit ≠ `0`, so tool errors turn the check red too, without
losing the comment. Permissions: `contents: read` +
`pull-requests: write` on the default `GITHUB_TOKEN`. Local use needs
no token at all.

## Adversarial review

A three-agent adversarial review (red-team / false-positive / code-audit)
was run on v0.1.0, followed by a full fix pass (v0.2.0, 2026-11-10).
Governing principle for the fix pass: **blocking verdicts (exit 1) require
strong evidence; uncertainty degrades to a warning or UNKNOWN, never a
block.** Tests grew 26 → 74, all offline (`FakeRegistry`; `LiveRegistry`
parsing is covered by mocked-`urlopen` tests against captured-shape
payloads in `tests/fixtures/registry-raw/`). Corpus grew 11 → 15 pairs.

| # | Finding (before) | Fix (after) |
|---|---|---|
| FP-1 | **Typo guard blocked real packages.** Any name within edit distance 1–2 of a popular name blocked, regardless of registry evidence — `preact`, `ms`, `pug`, `uid`, `numba`, `sympy`, `black` would all fail a PR. | Registry-exoneration policy: exists + (age ≥ 365 d OR weekly downloads ≥ 10 000) → `TYPO-LOOKALIKE` warning, exit 0 (PyPI: age alone, downloads unknown). Blocking `SUSPECT-TYPO` only for NOT-FOUND, or exists-but-young-and-low-download. Registry unknown → warning (unknown ≠ not-found). Regression tests for all seven named packages + `reqeusts` (still blocks) + young low-download near-name (still blocks). Live spot-check 2026-11-10: real `black` → `TYPO-LOOKALIKE`, age 3162 d, exit 0 (was: would block). |
| FP-2 | **Scoped typo compare leaked bare names.** `@babel/core` made `core` a "popular" name; scoped adds were also compared against bare popular names. | Comparison is form-preserving: scoped ↔ scoped only, unscoped ↔ unscoped only; no splitting on either side. `@angular/core`, `@nestjs/core`, `@vue/cli` with good registry data → clean; `@babel/cor` still matches `@babel/core`. |
| FP-3 | **Non-registry specs flagged NOT-FOUND.** `workspace:*`, `file:`, `git+…`, URL tarballs, `npm:` aliases etc. were sent to the registry and could block. | `is_nonregistry_spec` (prefixes `workspace: file: link: git+ git: http: https: npm: catalog: portal:` + PEP 508 `name @ url`) → `SKIPPED-NONREGISTRY`: never registry-audited, never NOT-FOUND, listed separately in text/JSON/comment, exit unaffected. Regression tests for every form. |
| FP-4 | **Diff-file mode was section-blind and monorepo-unsafe.** Any `"k": "v"` line in a `package.json` hunk parsed as a dep (script lines like `"build:prod"`, `engines`), pyproject quoted strings anywhere counted, and add/remove cancellation keyed by basename cancelled across different packages. | The diff parser tracks dependency-section context (package.json dep objects incl. brace depth; pyproject `*dependencies*` arrays under their table; requirements-style lines only in requirements hunks) and keys entries by (full manifest path, normalised name — PyPI PEP 503, npm lowercased). Script/engines lines and cross-package cancellations are regression-tested. |
| 5 | **Sections silently missed.** `peerDependencies`/`optionalDependencies`, PEP 735 `dependency-groups`, and poetry deps were not parsed — a peer→dependencies move looked like an addition, and the section was never shown. | All parsed in base AND head (dir, git, and diff modes), `section_hint` populated end-to-end (text + comment gain a Section column). `overrides` documented as out of scope in the README (transitive pins, not additions). |
| 6 | **Silent passes on broken input.** Present-but-unparseable or oversized manifests parsed as `{}`, invalid TOML fell through to a regex fallback that scanned every quoted string, and any git failure looked like "file absent". | Present-but-unparseable/oversized head or base manifest → exit 2 naming the file. Invalid TOML (tomllib) → exit 2, no fallback; the pre-3.11 fallback is scoped to `*dependencies*` arrays only. Git mode distinguishes absence (`ls-tree` empty after the ref verifies) from any other failure (→ exit 2). |
| 7 | **action.yml swallowed tool errors.** Enforce only failed on exit exactly `1` (a crash passed the gate), it lacked `if: always()`, the comment step crashed on a missing comment file, and comment lookup neither paginated nor used a robust marker. | Enforce (with `if: always()`) fails on ANY exit ≠ `"0"`; the comment step also gets `if: always()`, tolerates a missing comment file by posting an explanatory marker comment, paginates via `github.paginate` + `per_page: 100`, and matches the hidden marker `<!-- phantomdep-pr-gate -->` anywhere in the body (the marker is now emitted by `render_comment`). Static contract tests pin all of this. |
| 8 | **Registry clients trusted response shape.** Wrong-typed payloads could read as `exists=True`; a bool download count passed the int check; scoped npm URLs kept `/` unencoded; PyPI age counted yanked files and only the ISO timestamp field. | Shape validated before `exists=True` (dict + expected name/info, else UNKNOWN). Downloads require `type(x) is int` (bool excluded). Scoped URLs use `quote(..., safe="@")` (`@babel%2Fcore`). PyPI age = min over non-yanked files, `upload_time_iso_8601` with `upload_time` fallback; negative age → unknown. No retries — documented fail-open to UNKNOWN. Mocked-urlopen unit tests use real captured payload shapes. |
| 9 | **Import usage was slow and narrow.** The head tree was re-walked per dep (>30 s blowups), and `export … from`, Python comma/semicolon imports, `__import__`/`importlib`, `.pyi/.ipynb/.mts/.cts`, config-file references, and many PyPI aliases were missed → false `UNUSED`. | (a) `HeadTreeCache` walks the head tree once per run and is shared by all deps; audits capped at 200 added deps (extras `NOT-AUDITED`). (b) Patterns extended as listed. (c) Reference pass: a quoted/key occurrence in config-ish files (package.json *scripts*/tool-config only — the dep sections themselves are excluded so a dep can't "reference" itself — plus webpack/vite/eslint/babel/tailwind/postcss/tsconfig etc.) → `imported="referenced"`, no `UNUSED`. (d) Alias map extended: `mysqlclient→MySQLdb`, `djangorestframework→rest_framework`, `GitPython→git`, `pyserial→serial`, plus the previously present `opencv-python→cv2`, `protobuf→google.protobuf`, `attrs→attr/attrs`, `python-dateutil→dateutil`. (e) Symlinked files/dirs whose realpath escapes the head root are not followed. |
| 10 | **Diff-mode import scan could walk an unbounded CWD.** | Fixed by the shared cache (9a) plus a 20 000-file scan cap with a truncation note on stderr, and a one-line stderr note when the walk passes 5 000 files. |
| 11 | **Misc robustness.** `./localpkg` produced the dep name `-`; `\|` in a package name broke the comment table; malformed registry fixtures were silently skipped; `-r` includes vanished without a trace. | PEP 508 name validation in `parse_requirement_name`; `\|` escaped in comment table cells; `FakeRegistry.from_fixture_dir` type-validates fixtures (`exists` must be bool, downloads int-not-bool, …) and raises on malformed fixtures; `-r` includes print an explicit "not followed" warning. |
| 12 | **Popular list gaps/staleness.** | Added `dayjs`, `nanoid`, `three` (npm) and `typing-extensions`, `packaging`, `rich` (PyPI). Removed `pip`/`wheel` from the PyPI list: installer tooling, almost never genuine PR-added runtime deps, and a typo-noise source (documented in the code). |

**Corpus after the fix pass: 15 pairs** (original 11 unchanged in outcome —
10/10 added deps verdicted as planted, 2/2 non-additions empty — plus
12 typo-lookalike `preact`, 13 peer→deps move, 14 `workspace:*` skip,
15 scoped `@angular/core` clean). **74/74 tests pass**, fully offline.
Required spot-checks, all exit 0 with mock data: `preact`, `black`,
`workspace:*` dep, script-line-only diff, peer→dependencies move;
`reqeusts` still exits 1.

**Honestly unfixed / residual:** import-usage is still a grep (string-
concatenated or computed import names still evade; `UNUSED` remains
advisory-only, so this cannot block). The diff parser remains heuristic
for exotic hunk shapes (the exact dir/git modes are unaffected). The
200-dep cap and 20 000-file cap trade completeness for bounded runtime
and say so in the output (`NOT-AUDITED` / truncation note).
