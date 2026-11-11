#!/usr/bin/env python3
"""PhantomDep PR Gate — audit ONLY dependencies added in a PR diff.

Stdlib-only, deterministic given the same registry data, no LLM, no token.
Never executes scanned-repo code. Never installs anything.

Exit policy (exact):
  1  if any added dep is NOT-FOUND or a blocking SUSPECT-TYPO (see the typo
     policy below — registry-exonerated lookalikes never block)
  0  otherwise (warnings such as NEWBORN / LOW-DOWNLOADS / UNUSED /
     TYPO-LOOKALIKE are noted in the output but do not fail the gate;
     non-registry specs are SKIPPED-NONREGISTRY and never affect the exit)
  2  on usage / input errors (bad paths, present-but-unparseable or
     oversized manifests, git failures other than "path absent at ref",
     no input mode)

Typo policy (blocking requires strong evidence):
  * NOT-FOUND + close to a popular name   -> SUSPECT-TYPO (blocks, via
    NOT-FOUND as well)
  * exists, but young (<365 days) AND not popular (weekly downloads
    unknown or <10000; in --live mode PyPI weekly downloads come from
    pypistats, in offline mode they are unknown so age alone decides)
    -> SUSPECT-TYPO (blocks)
  * exists AND (age >= 365 days OR weekly downloads >= 10000)
    -> TYPO-LOOKALIKE (warning only — established packages such as
    preact, ms, pug, uid, numba, sympy, black are not typosquats)
  * registry unknown -> TYPO-LOOKALIKE (warning only; unknown is not
    evidence)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

VERSION = "0.2.0"
MAX_MANIFEST_BYTES = 2_000_000
MAX_DIFF_BYTES = 5_000_000
MAX_REGISTRY_BYTES = 2_000_000
MAX_SOURCE_FILE_BYTES = 1_000_000
NEWBORN_DAYS = 30
LOW_DOWNLOADS_WEEKLY = 1000
TYPO_EXONERATE_AGE_DAYS = 365
TYPO_EXONERATE_WEEKLY_DOWNLOADS = 10000
MAX_AUDITED_DEPS = 200
MAX_SCAN_FILES = 20000
SCAN_NOTE_FILES = 5000
MAX_MANIFESTS = 100
MAX_MANIFESTS_HARD = 1000  # beyond this: exit 2, never a silent partial load
# Dirs never searched for manifests, at ANY depth (tool/VCS generated,
# never a source package's own name in practice):
MANIFEST_EXCLUDED_DIRS_ANY_DEPTH = {".git", "node_modules", "__pycache__"}
# Dirs excluded only at the TOP level of the tree/ref: build outputs and
# venvs live there, but a nested dir with one of these names (e.g.
# packages/build) is a real package and IS audited (fix pass, v0.2).
MANIFEST_EXCLUDED_DIRS_TOP_LEVEL = {".venv", "venv", "dist", "build",
                                    ".tox", ".eggs", ".mypy_cache",
                                    ".pytest_cache"}
MANIFEST_EXCLUDED_DIRS = (MANIFEST_EXCLUDED_DIRS_ANY_DEPTH
                          | MANIFEST_EXCLUDED_DIRS_TOP_LEVEL)

MANIFEST_NAMES = ("package.json", "requirements.txt", "pyproject.toml")
COMMENT_MARKER = "<!-- phantomdep-pr-gate -->"

NONREGISTRY_PREFIXES = (
    "workspace:", "file:", "link:", "git+", "git:", "http:", "https:",
    "npm:", "catalog:", "portal:",
)

# ---------------------------------------------------------------- popular ---
POPULAR_NPM = [
    "react", "react-dom", "lodash", "axios", "express", "chalk", "debug",
    "commander", "moment", "uuid", "request", "webpack", "babel-core",
    "@babel/core", "typescript", "eslint", "prettier", "jest", "mocha",
    "chai", "vue", "angular", "next", "nuxt", "jquery", "underscore",
    "classnames", "prop-types", "redux", "zustand", "vite", "rollup",
    "esbuild", "ts-node", "nodemon", "cors", "dotenv", "zod", "yargs",
    "inquirer", "fs-extra", "glob", "rimraf", "semver", "ws", "socket.io",
    "mongoose", "pg", "mysql2", "prisma", "@types/node", "tailwindcss",
    # refreshed 2026-11-10 (fix pass): widely-used names that were missing
    "dayjs", "nanoid", "three",
]
POPULAR_PYPI = [
    "requests", "numpy", "pandas", "flask", "django", "fastapi", "pytest",
    # "pip"/"wheel" removed 2026-11-10: they are installer tooling, almost
    # never genuine PR-added runtime deps, and caused typo-noise.
    "setuptools", "boto3", "botocore", "urllib3", "certifi",
    "idna", "charset-normalizer", "pydantic", "sqlalchemy", "celery",
    "redis", "pillow", "scikit-learn", "scipy", "matplotlib", "tornado",
    "aiohttp", "httpx", "click", "tqdm", "pyyaml", "beautifulsoup4",
    "lxml", "selenium", "openpyxl", "xlrd", "cryptography", "paramiko",
    "jinja2", "markupsafe", "werkzeug", "gunicorn", "uvicorn", "starlette",
    "psycopg2", "pymongo", "protobuf", "grpcio", "transformers", "torch",
    # refreshed 2026-11-10 (fix pass)
    "typing-extensions", "packaging", "rich",
]

# package name -> import names (first is primary)
PYPI_ALIASES = {
    "scikit-learn": ["sklearn"],
    "pillow": ["PIL"],
    "beautifulsoup4": ["bs4"],
    "pyyaml": ["yaml"],
    "python-dateutil": ["dateutil"],
    "attrs": ["attr", "attrs"],
    "protobuf": ["google.protobuf"],
    "opencv-python": ["cv2"],
    "pyjwt": ["jwt"],
    "dnspython": ["dns"],
    "msgpack-python": ["msgpack"],
    "pycryptodome": ["Crypto"],
    "psycopg2-binary": ["psycopg2"],
    "mysql-connector-python": ["mysql.connector"],
    "google-cloud-storage": ["google.cloud.storage"],
    "mysqlclient": ["MySQLdb"],
    "djangorestframework": ["rest_framework"],
    "gitpython": ["git"],
    "pyserial": ["serial"],
}
NPM_ALIASES: dict[str, list[str]] = {}  # npm import name == package name


class ManifestError(Exception):
    """A present manifest could not be read/parsed, or git failed in a way
    other than 'path absent at ref'. Maps to exit 2 and names the file."""


# ------------------------------------------------------------- levenshtein ---
def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def closest_popular(name: str, ecosystem: str):
    """Return (popular_name, distance) for distance 1..2, else None.

    Scoped npm names are compared ONLY against scoped popular names, and
    unscoped names ONLY against unscoped popular names — the bare part of
    a scoped popular name (e.g. "core" from "@babel/core") is never a
    popular name in its own right.
    """
    popular = POPULAR_NPM if ecosystem == "npm" else POPULAR_PYPI
    norm = _norm_pypi(name) if ecosystem == "pypi" else name.lower()
    scoped = norm.startswith("@")
    pop_norm = {}
    for p in popular:
        pn = _norm_pypi(p) if ecosystem == "pypi" else p.lower()
        if ecosystem == "npm" and pn.startswith("@") != scoped:
            continue
        pop_norm[pn] = p
    if norm in pop_norm:
        return None
    best = None
    for pn, orig in pop_norm.items():
        if abs(len(pn) - len(norm)) > 2:
            continue
        d = levenshtein(norm, pn)
        if 1 <= d <= 2 and (best is None or d < best[1]):
            best = (orig, d)
    return best


def _norm_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _norm_key(ecosystem: str, name: str) -> str:
    return _norm_pypi(name) if ecosystem == "pypi" else name.lower()


# ------------------------------------------------------- name / spec utils ---
_VALID_NAME_RE = re.compile(r"[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]")


def _valid_dep_name(name: str) -> bool:
    return bool(name) and _VALID_NAME_RE.fullmatch(name) is not None


def is_nonregistry_spec(spec: str) -> bool:
    """True for specs that do not name a registry version at all:
    workspace:/file:/link:/git/http/npm:/catalog:/portal: specs and
    PEP 508 direct references (`name @ url`)."""
    if not spec:
        return False
    s = spec.strip()
    low = s.lower()
    if low.startswith(NONREGISTRY_PREFIXES):
        return True
    m = re.search(r"\s@\s*(\S+)", s)
    if m and m.group(1).lower().startswith(
            ("http:", "https:", "git+", "git:", "file:", "ssh:")):
        return True
    return False


# ------------------------------------------------------------ manifest parse -
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def parse_requirement_name(spec: str) -> str | None:
    """Extract the package name from a PEP 508-ish requirement string.

    Names are validated (PEP 508): junk like `./localpkg` (which used to
    yield the name "-"/".") returns None.
    """
    spec = spec.strip()
    if not spec or spec.startswith(
            ("#", "-", "git+", "http:", "https:", "file:", "./", "/", "~")):
        return None
    m = _REQ_NAME_RE.match(spec.split(";")[0].split("@")[0].strip())
    if not m:
        return None
    name = m.group(1).strip(".-_")
    # reject trailing extras brackets captured oddly etc: name token only
    name = name.split("[")[0]
    return name if _valid_dep_name(name) else None


@dataclass
class ManifestEntry:
    name: str            # as written in the manifest
    spec: str = ""
    section: str = ""


_PKG_DEP_SECTIONS = ("dependencies", "devDependencies",
                     "peerDependencies", "optionalDependencies")


def _parse_package_json_entries(text: str) -> dict[str, ManifestEntry]:
    data = json.loads(text)  # raises ValueError on invalid JSON (callers map to exit 2)
    out: dict[str, ManifestEntry] = {}
    if not isinstance(data, dict):
        return out
    for section in _PKG_DEP_SECTIONS:
        deps = data.get(section)
        if isinstance(deps, dict):
            for k, v in deps.items():
                if isinstance(k, str) and k:
                    out[k.lower()] = ManifestEntry(name=k, spec=str(v), section=section)
    return out


def parse_package_json(text: str) -> dict[str, str]:
    return {e.name: e.spec for e in _parse_package_json_entries(text).values()}


def _parse_requirements_entries(text: str) -> dict[str, ManifestEntry]:
    out: dict[str, ManifestEntry] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split(" #")[0].strip()
        if line.startswith("-r") or line.startswith("--requirement"):
            print(f"warning: requirements include '{line}' is not followed; "
                  "only that file itself is audited", file=sys.stderr)
            continue
        if line.startswith("-"):
            continue  # -e, --index-url etc are not plain deps
        name = parse_requirement_name(line)
        if name:
            out[_norm_pypi(name)] = ManifestEntry(
                name=name, spec=line, section="requirements")
    return out


def parse_requirements(text: str) -> dict[str, str]:
    return {_norm_pypi(e.name): e.spec
            for e in _parse_requirements_entries(text).values()}


def _spec_str(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):  # poetry-style {version = "^1.0", ...}
        v = value.get("version")
        return str(v) if v is not None else json.dumps(value, sort_keys=True)
    return str(value)


def _parse_pyproject_entries_tomllib(data: dict) -> dict[str, ManifestEntry]:
    out: dict[str, ManifestEntry] = {}

    def add(spec, section: str):
        if isinstance(spec, str):
            n = parse_requirement_name(spec)
            if n:
                out[_norm_pypi(n)] = ManifestEntry(name=n, spec=spec, section=section)

    project = data.get("project") or {}
    if isinstance(project, dict):
        for spec in project.get("dependencies") or []:
            add(spec, "project.dependencies")
        opt = project.get("optional-dependencies") or {}
        if isinstance(opt, dict):
            for group, specs in opt.items():
                for spec in specs or []:
                    add(spec, f"project.optional-dependencies.{group}")
    # PEP 735 dependency-groups: list of strings and/or {include-group=...}
    groups = data.get("dependency-groups") or {}
    if isinstance(groups, dict):
        for group, items in groups.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str):
                        add(item, f"dependency-groups.{group}")
    tool = data.get("tool") or {}
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        def add_poetry(deps, section: str):
            if isinstance(deps, dict):
                for name, value in deps.items():
                    if isinstance(name, str) and name.lower() != "python" \
                            and _valid_dep_name(name):
                        out[_norm_pypi(name)] = ManifestEntry(
                            name=name, spec=_spec_str(value), section=section)
        add_poetry(poetry.get("dependencies"), "tool.poetry.dependencies")
        add_poetry(poetry.get("dev-dependencies"),
                   "tool.poetry.dev-dependencies")
        pgroups = poetry.get("group") or {}
        if isinstance(pgroups, dict):
            for gname, gval in pgroups.items():
                if isinstance(gval, dict):
                    add_poetry(gval.get("dependencies"),
                               f"tool.poetry.group.{gname}.dependencies")
    return out


def _fallback_pyproject_entries(text: str) -> dict[str, ManifestEntry]:
    """Pre-3.11 fallback (no tomllib): scoped STRICTLY to arrays whose key
    mentions 'dependencies' — never a scan of every quoted string."""
    out: dict[str, ManifestEntry] = {}
    for m in re.finditer(r"([A-Za-z0-9_.-]*dependencies[A-Za-z0-9_.-]*)\s*=\s*\["
                         r"(.*?)\]", text, re.DOTALL | re.IGNORECASE):
        key, body = m.group(1), m.group(2)
        for q in re.finditer(r'"([^"]+)"|\'([^\']+)\'', body):
            spec = q.group(1) or q.group(2)
            n = parse_requirement_name(spec)
            if n:
                out[_norm_pypi(n)] = ManifestEntry(name=n, spec=spec, section=key)
    return out


def _parse_pyproject_entries(text: str) -> dict[str, ManifestEntry]:
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception as e:
            # Invalid TOML is an input error (exit 2 upstream) — never a
            # silent fall-through to the regex fallback.
            raise ValueError(f"invalid TOML: {e}") from e
        if not isinstance(data, dict):
            return {}
        return _parse_pyproject_entries_tomllib(data)
    return _fallback_pyproject_entries(text)


def parse_pyproject(text: str) -> dict[str, str]:
    return {_norm_pypi(e.name): e.spec
            for e in _parse_pyproject_entries(text).values()}


_ENTRY_PARSERS = {
    "package.json": _parse_package_json_entries,
    "requirements.txt": _parse_requirements_entries,
    "pyproject.toml": _parse_pyproject_entries,
}
PARSERS = {
    "package.json": ("npm", parse_package_json),
    "requirements.txt": ("pypi", parse_requirements),
    "pyproject.toml": ("pypi", parse_pyproject),
}
ECOSYSTEMS = {fname: eco for fname, (eco, _p) in PARSERS.items()}


def _entries_from_text(fname: str, text: str, where: str) -> dict[str, ManifestEntry]:
    try:
        return _ENTRY_PARSERS[fname](text)
    except ManifestError:
        raise
    except Exception as e:
        raise ManifestError(f"unparseable manifest {where}: {e}") from e


class ManifestMap(dict):
    """relpath -> {normalised key: ManifestEntry}, plus discovery metadata.

    `errors` maps relpath -> (content_sha256, message) for manifests that
    were present but unparseable/oversized/non-regular in collect mode;
    in strict mode the loaders raise instead of collecting.
    """
    def __init__(self):
        super().__init__()
        self.truncated = False
        self.total_found = 0
        self.errors: dict[str, tuple[str, str]] = {}


def _rel_excluded(rel: str) -> bool:
    """Excluded-dir test for a manifest relpath (see constants):
    any-depth names anywhere; top-level names only as the FIRST part."""
    parts = rel.replace(os.sep, "/").split("/")[:-1]
    for i, part in enumerate(parts):
        if part in MANIFEST_EXCLUDED_DIRS_ANY_DEPTH:
            return True
        if i == 0 and part in MANIFEST_EXCLUDED_DIRS_TOP_LEVEL:
            return True
    return False


def discover_manifests_in_dir(directory: str) -> list[str]:
    """All manifest relpaths under `directory`, any depth, sorted.

    Exclusions per _rel_excluded. Symlinked directories are NEVER
    followed (os.walk default), escaping or not — documented behaviour.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        rel_dir = os.path.relpath(dirpath, directory)
        depth = 0 if rel_dir == "." else len(rel_dir.split(os.sep))
        kept = []
        for d in dirnames:
            if d in MANIFEST_EXCLUDED_DIRS_ANY_DEPTH:
                continue
            if depth == 0 and d in MANIFEST_EXCLUDED_DIRS_TOP_LEVEL:
                continue
            kept.append(d)
        dirnames[:] = sorted(kept)
        for fname in sorted(filenames):
            if fname in MANIFEST_NAMES:
                found.append(os.path.relpath(os.path.join(dirpath, fname),
                                             directory))
    found.sort()
    return found


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _read_manifest_file(path: str, root: str) -> str:
    """Read a manifest file safely: must be a regular file (a FIFO/device
    raises instead of hanging), a symlink whose realpath escapes `root`
    raises, and the read itself is capped (never unbounded)."""
    import stat as _stat
    st = os.lstat(path)
    if os.path.islink(path):
        real = os.path.realpath(path)
        root_real = os.path.realpath(root)
        if not (real == root_real or real.startswith(root_real + os.sep)):
            raise ManifestError(
                f"manifest {path} is a symlink escaping the audited tree; "
                "refusing to follow it")
        st = os.stat(path)
    if not _stat.S_ISREG(st.st_mode):
        raise ManifestError(f"manifest {path} is not a regular file; refusing to read it")
    if st.st_size > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"manifest {path} is oversized ({st.st_size} bytes > "
            f"{MAX_MANIFEST_BYTES}); refusing to audit a partial parse")
    with open(path, "r", encoding="utf-8", errors="strict") as fh:
        text = fh.read(MAX_MANIFEST_BYTES + 1)
    if len(text) > MAX_MANIFEST_BYTES:
        raise ManifestError(f"manifest {path} is oversized; refusing a partial parse")
    return text


def load_manifests_from_dir(directory: str, collect_errors: bool = False) -> ManifestMap:
    """manifest relpath -> {normalised key: ManifestEntry}.

    Discovers manifests at ANY depth (monorepo recursion; see
    discover_manifests_in_dir for exclusions). Loads ALL discovered
    manifests (up to MAX_MANIFESTS_HARD, beyond which ManifestError);
    the MAX_MANIFESTS audit cap is applied SYMMETRICALLY by
    diff_manifests, never per-side here. In strict mode (default) a
    present-but-unparseable manifest raises ManifestError (exit 2
    naming the file); with collect_errors=True the failure is recorded
    in `.errors` for diff_manifests to enforce (an error identical in
    base and head is pre-existing and does not fail the run).
    """
    result = ManifestMap()
    relpaths = discover_manifests_in_dir(directory)
    result.total_found = len(relpaths)
    result.truncated = len(relpaths) > MAX_MANIFESTS
    if len(relpaths) > MAX_MANIFESTS_HARD:
        raise ManifestError(
            f"{len(relpaths)} manifests under {directory} exceeds the hard "
            f"cap of {MAX_MANIFESTS_HARD}; refusing a partial audit")
    for rel in relpaths:
        fname = os.path.basename(rel)
        path = os.path.join(directory, rel)
        try:
            try:
                text = _read_manifest_file(path, directory)
            except (OSError, UnicodeDecodeError) as e:
                raise ManifestError(f"cannot read manifest {path}: {e}") from e
            result[rel] = _entries_from_text(fname, text, path)
        except ManifestError as e:
            if not collect_errors:
                raise
            raw = ""
            try:
                with open(path, "rb") as fh:
                    raw = fh.read(MAX_MANIFEST_BYTES + 1).decode("utf-8", "surrogateescape")
            except OSError:
                raw = ""
            result.errors[rel] = (_content_hash(raw), str(e))
    return result


@dataclass
class AddedDep:
    name: str            # as written in head manifest
    ecosystem: str       # npm | pypi
    manifest: str
    version_spec: str = ""
    section_hint: str = ""
    manifest_path: str = ""   # full path (diff mode); defaults to manifest


class AddedList(list):
    """list[AddedDep] plus manifest-cap metadata for honest reporting."""
    manifest_truncated = False
    manifest_total = 0
    manifest_audited = 0


def _entries_signature(entries: dict) -> tuple:
    return tuple(sorted((k, getattr(v, "spec", str(v)),
                         getattr(v, "section", "")) for k, v in entries.items()))


def diff_manifests(base: dict, head: dict) -> AddedList:
    """Added = present in head, absent in base for the SAME manifest
    relpath (any section — a dep moving peerDependencies -> dependencies
    is NOT an addition). Version bumps are NOT adds. Manifests present
    only in head contribute all their deps; only in base are ignored.
    Comparison keys are normalised (PyPI PEP 503, npm lowercased).

    v0.2 fix pass:
    * The MAX_MANIFESTS cap is applied to the UNION of relpaths, the
      same selected set on both sides — a manifest dropped by the cap
      is dropped from base AND head, so truncation can never manufacture
      an "added" dep (it is reported via AddedList.manifest_truncated).
    * Rename pairing: a head-only manifest is paired with a base-only
      manifest of the same filename when exactly one candidate exists,
      or when a candidate's entries are identical — a pure move is not
      a re-audit of every dep.
    * Collected manifest errors (collect_errors mode): a head error
      whose content differs from base's error at the same relpath
      raises ManifestError; an identical error in both is pre-existing
      and skipped.
    """
    head_errors = getattr(head, "errors", {}) or {}
    base_errors = getattr(base, "errors", {}) or {}
    for rel, (h, msg) in sorted(head_errors.items()):
        if base_errors.get(rel, (None, ""))[0] != h:
            raise ManifestError(msg)
    union = sorted(set(base) | set(head))
    truncated = len(union) > MAX_MANIFESTS
    selected = set(union[:MAX_MANIFESTS]) if truncated else set(union)
    # rename pairing among unselected-by-exact-path manifests
    paired_base_for: dict[str, str] = {}
    base_only = [k for k in selected if k not in head and k in base]
    head_only = [k for k in selected if k not in base and k in head]
    used_base: set[str] = set()
    for hk in sorted(head_only):
        cands = [bk for bk in base_only
                 if bk not in used_base and os.path.basename(bk) == os.path.basename(hk)]
        pick = None
        identical = [bk for bk in cands
                     if _entries_signature(base[bk]) == _entries_signature(head[hk])]
        if len(identical) == 1:
            pick = identical[0]
        elif len(cands) == 1:
            pick = cands[0]
        if pick is not None:
            paired_base_for[hk] = pick
            used_base.add(pick)
    added = AddedList()
    added.manifest_truncated = truncated
    added.manifest_total = len(union)
    added.manifest_audited = len(selected)
    for key_path in sorted(head):
        if key_path not in selected:
            continue
        fname = os.path.basename(key_path)
        if fname not in ECOSYSTEMS:
            continue
        ecosystem = ECOSYSTEMS[fname]
        base_entries = base.get(key_path)
        if base_entries is None and key_path in paired_base_for:
            base_entries = base[paired_base_for[key_path]]
        base_keys = set((base_entries or {}).keys())
        for key in sorted(head[key_path], key=str.lower):
            if key not in base_keys:
                e = head[key_path][key]
                if isinstance(e, ManifestEntry):
                    added.append(AddedDep(name=e.name, ecosystem=ecosystem,
                                          manifest=fname, version_spec=e.spec,
                                          section_hint=e.section,
                                          manifest_path=key_path))
                else:  # plain {name: spec} dicts (back-compat)
                    added.append(AddedDep(name=str(key), ecosystem=ecosystem,
                                          manifest=fname, version_spec=str(e),
                                          manifest_path=key_path))
    added.sort(key=lambda d: (d.manifest_path, d.name.lower()))
    return added


# --------------------------------------------------------------- diff file ---
_DEP_SECTION_NAMES = set(_PKG_DEP_SECTIONS)
_PY_DEP_TABLES = ("project", "dependency-groups")


def parse_diff_file(text: str) -> list[AddedDep]:
    """Unified-diff parser with dependency-section context.

    * package.json: only `"name": "spec"` lines INSIDE a dependencies /
      devDependencies / peerDependencies / optionalDependencies object
      count — script lines ("build:prod"), engines, etc. never parse.
    * pyproject.toml: only quoted strings inside a *dependencies* array
      (project.dependencies / optional-dependencies / dependency-groups /
      poetry) count.
    * requirements.txt: requirement-style lines count.
    * Entries are keyed by (full manifest path, normalised name), so the
      same package added in one package and removed in another in a
      monorepo does not cancel out.
    * A name both added and removed at the same path cancels (version bump).
    """
    if len(text.encode("utf-8", "ignore")) > MAX_DIFF_BYTES:
        text = text[:MAX_DIFF_BYTES]
    added: dict[tuple[str, str], AddedDep] = {}
    removed: set[tuple[str, str]] = set()
    current_path: str | None = None
    # package.json section state
    pj_section: str | None = None
    pj_depth = 0
    # pyproject array state
    py_in_array = False
    py_section = ""
    py_table = ""

    def reset_context():
        nonlocal pj_section, pj_depth, py_in_array, py_section, py_table
        pj_section, pj_depth, py_in_array, py_section, py_table = None, 0, False, "", ""

    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            current_path = parts[1].strip() if len(parts) == 2 else None
            if current_path is not None and \
                    os.path.basename(current_path) not in PARSERS:
                current_path = None
            reset_context()
            continue
        if line.startswith("+++ "):
            tail = line[4:].strip()
            if tail == "/dev/null":
                current_path = None
            else:
                p = tail[2:] if tail.startswith("b/") else tail
                current_path = p if os.path.basename(p) in PARSERS else None
            reset_context()
            continue
        if line.startswith("@@"):
            reset_context()
            continue
        if current_path is None or line.startswith("---"):
            continue
        if not line or line[0] not in "+- ":
            continue
        sign, content = line[0], line[1:]
        fname = os.path.basename(current_path)
        ecosystem = ECOSYSTEMS[fname]

        deps: list[AddedDep] = []
        if fname == "package.json":
            deps, pj_section, pj_depth = _diff_package_json_line(
                content, sign, pj_section, pj_depth)
        elif fname == "pyproject.toml":
            deps, py_in_array, py_section, py_table = _diff_pyproject_line(
                content, sign, py_in_array, py_section, py_table)
        else:  # requirements.txt
            if sign in "+-":
                stripped = content.strip().split(" #")[0].strip()
                if stripped and not stripped.startswith("-"):
                    n = parse_requirement_name(stripped)
                    if n:
                        deps = [AddedDep(name=n, ecosystem=ecosystem,
                                         manifest=fname, version_spec=stripped,
                                         section_hint="requirements",
                                         manifest_path=current_path)]
        for dep in deps:
            dep.manifest_path = current_path
            key = (current_path, _norm_key(ecosystem, dep.name))
            if sign == "+":
                added[key] = dep
            elif sign == "-":
                removed.add(key)
    return [d for k, d in sorted(added.items()) if k not in removed]


def _diff_package_json_line(content: str, sign: str, section, depth):
    """Returns (deps_on_this_line, new_section, new_depth)."""
    stripped = content.strip()
    deps: list[AddedDep] = []
    # Whole-line JSON object (single-line package.json replacement)
    if sign in "+-" and stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and any(s in data for s in _DEP_SECTION_NAMES):
            for sec in _DEP_SECTION_NAMES:
                d = data.get(sec)
                if isinstance(d, dict):
                    for k, v in d.items():
                        if isinstance(k, str):
                            deps.append(AddedDep(name=k, ecosystem="npm",
                                                 manifest="package.json",
                                                 version_spec=str(v),
                                                 section_hint=sec))
            return deps, section, depth
    # Section header: "dependencies": {   (or inline { ... } / {})
    m = re.match(r'"([^"]+)"\s*:\s*(.*)$', stripped)
    if m and m.group(1) in _DEP_SECTION_NAMES:
        sec, rest = m.group(1), m.group(2).strip()
        if rest.startswith("{"):
            if "}" in rest:  # inline object on one line
                for k, v in re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"', rest):
                    if k != sec:
                        deps.append(AddedDep(name=k, ecosystem="npm",
                                             manifest="package.json",
                                             version_spec=v, section_hint=sec))
                return deps, None, 0
            return deps, sec, rest.count("{") - rest.count("}")
        return deps, section, depth
    if section is not None:
        if sign in "+-" and m and m.group(2).startswith('"'):
            vm = re.match(r'"([^"]*)"', m.group(2))
            deps.append(AddedDep(name=m.group(1), ecosystem="npm",
                                 manifest="package.json",
                                 version_spec=vm.group(1) if vm else "",
                                 section_hint=section))
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            return deps, None, 0
        return deps, section, depth
    # Outside any dependency section: scripts/engines/etc never parse.
    return deps, section, depth


def _diff_pyproject_line(content: str, sign: str, in_array, section, table):
    stripped = content.strip()
    deps: list[AddedDep] = []
    tm = re.match(r"\[(.+)\]\s*$", stripped)
    if tm:
        return deps, False, section, tm.group(1).strip()
    am = re.match(r"([A-Za-z0-9_.-]*)\s*=\s*\[(.*)$", stripped)
    if am and "dependencies" in am.group(1).lower():
        key = am.group(1)
        if table.startswith("project.optional-dependencies"):
            sec = f"project.optional-dependencies.{key}" if key != "optional-dependencies" else "project.optional-dependencies"
            sec = "project.optional-dependencies"
        elif table == "project":
            sec = "project.dependencies"
        elif table.startswith("dependency-groups"):
            sec = f"dependency-groups.{key}"
        elif "poetry" in table:
            sec = f"{table}.dependencies"
        else:
            sec = key
        deps.extend(_pyproject_array_deps(am.group(2), sec))
        if "]" in am.group(2):
            return deps, False, sec, table
        return deps, True, sec, table
    if in_array:
        deps.extend(_pyproject_array_deps(stripped, section))
        if "]" in stripped:
            return deps, False, section, table
        return deps, True, section, table
    return deps, in_array, section, table


def _pyproject_array_deps(fragment: str, section: str) -> list[AddedDep]:
    deps = []
    body = fragment.split("]")[0]
    for q in re.finditer(r'"([^"]+)"|\'([^\']+)\'', body):
        spec = q.group(1) or q.group(2)
        n = parse_requirement_name(spec)
        if n:
            deps.append(AddedDep(name=n, ecosystem="pypi",
                                 manifest="pyproject.toml", version_spec=spec,
                                 section_hint=section))
    return deps


def _deps_from_diff_line(content: str, fname: str, ecosystem: str) -> list[AddedDep]:
    """Back-compat helper: parse one manifest diff content line in isolation."""
    content = content.strip().rstrip(",")
    if fname == "package.json":
        deps, _, _ = _diff_package_json_line(
            '"dependencies": {' + content + "}", "+", None, 0) \
            if not content.startswith("{") else _diff_package_json_line(content, "+", None, 0)
        return deps
    stripped = content.strip('"').strip("'").rstrip(",").strip().strip('"').strip("'")
    name = parse_requirement_name(stripped)
    if name:
        return [AddedDep(name=name, ecosystem=ecosystem, manifest=fname,
                         version_spec=stripped)]
    return []


def _dep_from_diff_line(content: str, fname: str, ecosystem: str) -> AddedDep | None:
    deps = _deps_from_diff_line(content, fname, ecosystem)
    return deps[0] if deps else None


# ------------------------------------------------------------ git ref mode ---
def _git(args: list[str], cwd: str):
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise ManifestError(f"git {' '.join(args)} failed to run: {e}") from e


def _git_show(ref: str, path: str, cwd: str) -> str:
    r = _git(["show", f"{ref}:{path}"], cwd)
    if r.returncode != 0:
        raise ManifestError(
            f"git show {ref}:{path} failed although the path exists at "
            f"that ref: {r.stderr.strip()[:200]}")
    if len(r.stdout) > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"manifest {path} at {ref} is oversized "
            f"(> {MAX_MANIFEST_BYTES} bytes)")
    return r.stdout


def _git_list_manifests(ref: str, cwd: str) -> tuple[list[str], list[str]]:
    """(manifest relpaths, symlinked-manifest relpaths) at `ref`.

    Uses `git ls-tree -r -z` (NUL-terminated, unquoted) so paths with
    spaces, quotes, newlines or non-ASCII names are returned verbatim —
    the default C-quoted form silently hid such manifests in early v0.2.
    Symlinked manifests (mode 120000) are reported separately: their blob
    is a target path, not manifest content, and is never parsed as one.
    """
    r = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd)
    if r.returncode != 0:
        raise ManifestError(
            f"git ref '{ref}' is not readable in {cwd} "
            f"({r.stderr.strip()[:200] or 'unknown git error'})")
    r = _git(["ls-tree", "-r", "-z", ref], cwd)
    if r.returncode != 0:
        raise ManifestError(
            f"git ls-tree failed at {ref}: {r.stderr.strip()[:200]}")
    paths: list[str] = []
    links: list[str] = []
    for entry in r.stdout.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        mode = meta.split(" ", 1)[0]
        if os.path.basename(path) not in MANIFEST_NAMES or _rel_excluded(path):
            continue
        (links if mode == "120000" else paths).append(path)
    return sorted(paths), sorted(links)


def load_manifests_from_ref(ref: str, cwd: str,
                            collect_errors: bool = False) -> ManifestMap:
    """Manifests at ANY depth at `ref`, paired by relative path.

    See _git_list_manifests for path handling. Symlinked manifests are
    SKIPPED with a stderr note (never parsed, never an error). Loads all
    discovered manifests up to MAX_MANIFESTS_HARD (beyond: ManifestError);
    the MAX_MANIFESTS audit cap is applied symmetrically by
    diff_manifests. collect_errors behaves as in load_manifests_from_dir.
    """
    relpaths, links = _git_list_manifests(ref, cwd)
    for rel in links:
        print(f"note: symlinked manifest {ref}:{rel} is skipped "
              "(git mode does not follow manifest symlinks)", file=sys.stderr)
    result = ManifestMap()
    result.total_found = len(relpaths)
    result.truncated = len(relpaths) > MAX_MANIFESTS
    if len(relpaths) > MAX_MANIFESTS_HARD:
        raise ManifestError(
            f"{len(relpaths)} manifests at {ref} exceeds the hard cap of "
            f"{MAX_MANIFESTS_HARD}; refusing a partial audit")
    for rel in relpaths:
        fname = os.path.basename(rel)
        text = _git_show(ref, rel, cwd)
        try:
            result[rel] = _entries_from_text(fname, text, f"{ref}:{rel}")
        except ManifestError as e:
            if not collect_errors:
                raise
            result.errors[rel] = (_content_hash(text), str(e))
    return result


# ---------------------------------------------------------------- registry ---
@dataclass
class RegistryInfo:
    exists: bool | None          # None = unknown (lookup failed / offline)
    created: str | None = None   # ISO date/datetime of first publish
    weekly_downloads: int | None = None  # None = unknown (never fabricated)
    error: str | None = None


class FakeRegistry:
    """Offline registry injected in tests. Data: {(eco, norm_name): RegistryInfo}

    Fixture files are type-validated: a malformed fixture (exists not a
    bool, weekly_downloads not an int, unparseable JSON, missing
    ecosystem/name) raises ValueError — a test error, never a silent skip.
    """

    def __init__(self, data: dict | None = None):
        self._data: dict[tuple[str, str], RegistryInfo] = {}
        for (eco, name), info in (data or {}).items():
            self._data[(eco, self._key(eco, name))] = info

    @staticmethod
    def _key(eco: str, name: str) -> str:
        return _norm_pypi(name) if eco == "pypi" else name.lower()

    @classmethod
    def from_fixture_dir(cls, directory: str) -> "FakeRegistry":
        data = {}
        if directory and os.path.isdir(directory):
            for fname in sorted(os.listdir(directory)):
                if not fname.endswith(".json"):
                    continue
                path = os.path.join(directory, fname)
                try:
                    with open(path, encoding="utf-8") as fh:
                        raw = json.load(fh)
                except (OSError, ValueError) as e:
                    raise ValueError(f"malformed registry fixture {path}: {e}") from e
                if not isinstance(raw, dict):
                    raise ValueError(f"malformed registry fixture {path}: not an object")
                eco, name = raw.get("ecosystem"), raw.get("name")
                if eco not in ("npm", "pypi") or not isinstance(name, str) or not name:
                    raise ValueError(
                        f"malformed registry fixture {path}: bad ecosystem/name")
                exists = raw.get("exists")
                if exists is not None and not isinstance(exists, bool):
                    raise ValueError(
                        f"malformed registry fixture {path}: exists must be bool")
                weekly = raw.get("weekly_downloads")
                if weekly is not None and type(weekly) is int and weekly < 0:
                    raise ValueError(
                        f"malformed registry fixture {path}: "
                        "weekly_downloads must not be negative")
                if weekly is not None and type(weekly) is not int:
                    raise ValueError(
                        f"malformed registry fixture {path}: "
                        "weekly_downloads must be int or null")
                created = raw.get("created")
                if created is not None and not isinstance(created, str):
                    raise ValueError(
                        f"malformed registry fixture {path}: created must be str or null")
                data[(eco, name)] = RegistryInfo(
                    exists=exists, created=created,
                    weekly_downloads=weekly, error=raw.get("error"))
        return cls(data)

    def lookup(self, ecosystem: str, name: str) -> RegistryInfo:
        info = self._data.get((ecosystem, self._key(ecosystem, name)))
        if info is None:
            return RegistryInfo(exists=False)
        return info


class LiveRegistry:
    """Real registry lookups via urllib. Failures degrade to unknown.
    No retries: a failed lookup is UNKNOWN (fail-open, warning only)."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._cache: dict[tuple[str, str], RegistryInfo] = {}

    def _get_json(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": f"phantomdep/{VERSION}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read(MAX_REGISTRY_BYTES + 1)
        if len(body) > MAX_REGISTRY_BYTES:
            raise ValueError("registry response too large")
        return json.loads(body.decode("utf-8", "replace"))

    def lookup(self, ecosystem: str, name: str) -> RegistryInfo:
        # One lookup per (ecosystem, normalised name) per run: the same
        # dep added in N monorepo manifests must get ONE consistent
        # answer (and one pair of HTTP calls), not N chances for a
        # rate-limit failure to flip a verdict between manifests.
        key = (ecosystem, _norm_key(ecosystem, name))
        if key in self._cache:
            return self._cache[key]
        info = self._lookup_uncached(ecosystem, name)
        self._cache[key] = info
        return info

    def _lookup_uncached(self, ecosystem: str, name: str) -> RegistryInfo:
        try:
            if ecosystem == "npm":
                return self._npm(name)
            return self._pypi(name)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return RegistryInfo(exists=False)
            return RegistryInfo(exists=None, error=f"http {e.code}")
        except Exception as e:  # network, parse, timeout -> unknown, never crash
            return RegistryInfo(exists=None, error=type(e).__name__)

    def _npm(self, name: str) -> RegistryInfo:
        # Scoped names: the "/" must be %2F-encoded (quote safe="@").
        quoted = urllib.parse.quote(name, safe="@")
        data = self._get_json(f"https://registry.npmjs.org/{quoted}")
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return RegistryInfo(exists=None, error="malformed-response")
        created = None
        if isinstance(data.get("time"), dict):
            c = data["time"].get("created")
            created = c if isinstance(c, str) else None
        downloads = None
        try:
            dl = self._get_json(
                "https://api.npmjs.org/downloads/point/last-week/"
                + urllib.parse.quote(name, safe="@"))
            # bool is not an int: require the exact type.
            if isinstance(dl, dict) and type(dl.get("downloads")) is int \
                    and 0 <= dl["downloads"] <= 10**12:
                downloads = dl["downloads"]
        except Exception:
            downloads = None
        return RegistryInfo(exists=True, created=created, weekly_downloads=downloads)

    def _pypi(self, name: str) -> RegistryInfo:
        quoted = urllib.parse.quote(_norm_pypi(name), safe="-")
        data = self._get_json(f"https://pypi.org/pypi/{quoted}/json")
        if not isinstance(data, dict) or not isinstance(data.get("info"), dict) \
                or not isinstance(data["info"].get("name"), str):
            return RegistryInfo(exists=None, error="malformed-response")
        created = None
        releases = data.get("releases")
        if isinstance(releases, dict):
            times = []
            for files in releases.values():
                if isinstance(files, list):
                    for f in files:
                        if not isinstance(f, dict) or f.get("yanked") is True:
                            continue  # yanked files don't date the package
                        t = f.get("upload_time_iso_8601") or f.get("upload_time")
                        if isinstance(t, str) and t:
                            times.append(t)
            if times:
                created = min(times)
        # PyPI JSON API has no download counts; in --live mode only, try
        # pypistats. Any failure degrades to unknown, never a crash and
        # never a fabricated number.
        downloads = self._pypistats_weekly_downloads(name)
        return RegistryInfo(exists=True, created=created,
                            weekly_downloads=downloads)

    def _pypistats_weekly_downloads(self, name: str) -> int | None:
        """Weekly downloads from pypistats.org's documented public API:
        GET https://pypistats.org/api/packages/<name>/recent ->
        {"data": {"last_day": int, "last_week": int, "last_month": int},
         "package": str, "type": "recent_downloads"}.
        Name is PEP 503 normalised. Shape is validated defensively and
        last_week must be exactly int (bool excluded)."""
        try:
            quoted = urllib.parse.quote(_norm_pypi(name), safe="-")
            data = self._get_json(
                f"https://pypistats.org/api/packages/{quoted}/recent")
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        # The payload must be ABOUT the requested package: a mismatched
        # "package" (or a wrong "type", when present) is not evidence
        # about this name and must never exonerate it.
        pkg = data.get("package")
        if not isinstance(pkg, str) or _norm_pypi(pkg) != _norm_pypi(name):
            return None
        ptype = data.get("type")
        if ptype is not None and ptype != "recent_downloads":
            return None
        inner = data.get("data")
        if not isinstance(inner, dict):
            return None
        value = inner.get("last_week")
        if type(value) is not int or not (0 <= value <= 10**12):
            return None  # bool, string, negative or absurd: not a count
        return value


# ------------------------------------------------------------ import usage ---
_JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
            ".mts", ".cts"}
_PY_EXTS = {".py", ".pyi", ".ipynb"}
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist"}
_CONFIG_PREFIXES = ("webpack", "vite", "rollup", "eslint", "babel",
                    "tailwind", "postcss", "tsconfig", ".eslintrc",
                    ".babelrc", "jest", "next.config", "nuxt.config")


def _is_config_file(fname: str) -> bool:
    low = fname.lower()
    if low == "package.json":
        return True
    return any(low.startswith(p) for p in _CONFIG_PREFIXES) or ".config." in low


def _package_json_reference_text(text: str) -> str:
    """Reference text for package.json: scripts + tool-config blocks only.
    The dependency sections themselves are excluded — otherwise every dep
    would trivially 'reference' itself."""
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    keep = {k: v for k, v in data.items()
            if k not in _DEP_SECTION_NAMES and k not in ("name", "version")}
    try:
        return json.dumps(keep)
    except (TypeError, ValueError):
        return ""


class HeadTreeCache:
    """The head tree walked ONCE per run: source texts + config reference
    texts are cached and shared by every dep's import check."""

    def __init__(self, root: str | None):
        self.root = root
        self.files: list[tuple[str, str]] = []        # (ext, text)
        self.config_texts: list[str] = []
        self.file_count = 0
        self.truncated = False
        self._built = False
        self._noted_large = False

    def ensure(self):
        if self._built:
            return
        self._built = True
        if not self.root or not os.path.isdir(self.root):
            return
        root_real = os.path.realpath(self.root)
        for dirpath, dirnames, filenames in os.walk(self.root):
            kept = []
            for d in dirnames:
                if d in _SKIP_DIRS:
                    continue
                full = os.path.join(dirpath, d)
                if os.path.islink(full) and not os.path.realpath(full).startswith(
                        root_real + os.sep):
                    continue  # symlinked dir escaping the tree: not followed
                kept.append(d)
            dirnames[:] = kept
            for fname in filenames:
                if self.file_count >= MAX_SCAN_FILES:
                    if not self.truncated:
                        self.truncated = True
                        print(f"note: import scan truncated at {MAX_SCAN_FILES} "
                              "files; later files were not scanned",
                              file=sys.stderr)
                    return
                path = os.path.join(dirpath, fname)
                if os.path.islink(path) and not os.path.realpath(path).startswith(
                        root_real + os.sep):
                    continue  # symlinked file escaping head root: not followed
                ext = os.path.splitext(fname)[1].lower()
                is_src = ext in _JS_EXTS | _PY_EXTS
                is_cfg = _is_config_file(fname)
                if not is_src and not is_cfg:
                    continue
                text = _read_capped(path, MAX_SOURCE_FILE_BYTES)
                if text is None:
                    continue
                self.file_count += 1
                if self.file_count > SCAN_NOTE_FILES and not self._noted_large:
                    self._noted_large = True
                    print(f"note: import scan is walking a large tree "
                          f"(> {SCAN_NOTE_FILES} files under {self.root})",
                          file=sys.stderr)
                if is_src:
                    self.files.append((ext, text))
                if is_cfg:
                    self.config_texts.append(
                        _package_json_reference_text(text)
                        if fname == "package.json" else text)


def _read_capped(path: str, cap: int) -> str | None:
    try:
        if os.path.getsize(path) > cap:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(cap + 1)
    except OSError:
        return None


def import_names_for(dep: AddedDep) -> tuple[list[str], bool]:
    """(candidate import names, alias_is_known)"""
    if dep.ecosystem == "pypi":
        key = _norm_pypi(dep.name)
        if key in PYPI_ALIASES:
            return PYPI_ALIASES[key], True
        return [dep.name, _norm_pypi(dep.name).replace("-", "_")], False
    return [dep.name], True


def _import_patterns(name: str) -> list[re.Pattern]:
    esc = re.escape(name)
    return [
        # JS/TS: import ... from 'x' | import 'x' | require('x') | import('x')
        #        export ... from 'x'
        re.compile(r"""(?:import\s+[^'"]*?from\s*|export\s+[^'"]*?from\s*|"""
                   r"""import\s+|require\s*\(\s*|import\s*\(\s*)['"]"""
                   + esc + r"""(?:/[^'"]*)?['"]"""),
        # Python: import x / from x import ... at line start or after ';',
        # submodule dots
        re.compile(r"(?:^|;)\s*(?:import|from)\s+" + esc + r"(?:\.|\s|,|;|$)",
                   re.MULTILINE),
        # Python comma lists: import a, b, x
        re.compile(r"(?:^|;)\s*import\s+(?:[A-Za-z_][\w.]*\s*,\s*)+"
                   + esc + r"(?:\.|\s|,|;|$)", re.MULTILINE),
        # Python anywhere-form (ipynb JSON, semicolon chains): import x
        re.compile(r"\bimport\s+" + esc + r"(?:\.|\s|,|;|\"|$)"),
        # __import__('x') / importlib.import_module('x')
        re.compile(r"""(?:__import__|import_module)\s*\(\s*['"]"""
                   + esc + r"""(?:\.|/[^'"]*)?['"]"""),
    ]


def check_imported(dep: AddedDep, head_dir: str | None,
                   cache: HeadTreeCache | None = None):
    """True = imported, "referenced" = named in a config file,
    False = definitely not, None = unknown."""
    if not head_dir or not os.path.isdir(head_dir):
        return None
    if cache is None:
        cache = HeadTreeCache(head_dir)
    cache.ensure()
    names, alias_known = import_names_for(dep)
    patterns = [p for n in names for p in _import_patterns(n)]
    for _ext, text in cache.files:
        for pat in patterns:
            if pat.search(text):
                return True
    # Reference pass: quoted occurrence / object key in config-ish files,
    # including command strings that start with the name ("axios --help").
    for text in cache.config_texts:
        for n in names + [dep.name]:
            if re.search(r"""['"]""" + re.escape(n) + r"""(?:['"]|/|\s)""", text):
                return "referenced"
    if dep.ecosystem == "pypi" and not alias_known and "-" in dep.name:
        return None
    return False


# ------------------------------------------------------------------ audit ----
@dataclass
class DepResult:
    name: str
    ecosystem: str
    manifest: str
    version_spec: str
    exists: bool | None
    age_days: int | None
    weekly_downloads: int | None
    typo_match: str | None
    typo_distance: int | None
    imported: object  # bool | "referenced" | None
    verdicts: list[str] = field(default_factory=list)
    section: str = ""
    manifest_path: str = ""

    @property
    def blocking(self) -> bool:
        return "NOT-FOUND" in self.verdicts or "SUSPECT-TYPO" in self.verdicts


def _parse_date(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def audit_dep(dep: AddedDep, registry, head_dir: str | None,
              now: datetime | None = None,
              cache: HeadTreeCache | None = None) -> DepResult:
    now = now or datetime.now(timezone.utc)
    # Non-registry specs are never audited against a registry.
    if is_nonregistry_spec(dep.version_spec):
        imported = check_imported(dep, head_dir, cache=cache)
        return DepResult(
            name=dep.name, ecosystem=dep.ecosystem, manifest=dep.manifest,
            version_spec=dep.version_spec, exists=None, age_days=None,
            weekly_downloads=None, typo_match=None, typo_distance=None,
            imported=imported, verdicts=["SKIPPED-NONREGISTRY"],
            section=dep.section_hint, manifest_path=dep.manifest_path or dep.manifest)
    info = registry.lookup(dep.ecosystem, dep.name)
    created_dt = _parse_date(info.created)
    age_days = (now - created_dt).days if created_dt else None
    if age_days is not None and age_days < 0:
        age_days = None  # registry clock nonsense -> unknown, never NEWBORN
    typo = closest_popular(dep.name, dep.ecosystem)
    imported = check_imported(dep, head_dir, cache=cache)
    verdicts: list[str] = []
    if info.exists is False:
        verdicts.append("NOT-FOUND")
    elif info.exists is None:
        verdicts.append("UNKNOWN-REGISTRY")
    if typo is not None:
        if info.exists is False:
            verdicts.append("SUSPECT-TYPO")
        elif info.exists is True:
            exonerated = (
                (age_days is not None and age_days >= TYPO_EXONERATE_AGE_DAYS)
                or (type(info.weekly_downloads) is int
                    and info.weekly_downloads >= TYPO_EXONERATE_WEEKLY_DOWNLOADS))
            verdicts.append("TYPO-LOOKALIKE" if exonerated else "SUSPECT-TYPO")
        else:
            # Registry unknown: a name resemblance alone is not evidence.
            verdicts.append("TYPO-LOOKALIKE")
    if age_days is not None and age_days < NEWBORN_DAYS:
        verdicts.append("NEWBORN")
    if type(info.weekly_downloads) is int \
            and info.weekly_downloads < LOW_DOWNLOADS_WEEKLY:
        verdicts.append("LOW-DOWNLOADS")
    if imported is False:
        verdicts.append("UNUSED")
    if not verdicts:
        verdicts.append("OK")
    return DepResult(
        name=dep.name, ecosystem=dep.ecosystem, manifest=dep.manifest,
        version_spec=dep.version_spec, exists=info.exists, age_days=age_days,
        weekly_downloads=info.weekly_downloads if type(info.weekly_downloads) is int
        else (info.weekly_downloads if info.weekly_downloads is None else None),
        typo_match=typo[0] if typo else None,
        typo_distance=typo[1] if typo else None,
        imported=imported, verdicts=verdicts,
        section=dep.section_hint, manifest_path=dep.manifest_path or dep.manifest)


def audit(added: list[AddedDep], registry, head_dir: str | None,
          now: datetime | None = None) -> list[DepResult]:
    cache = HeadTreeCache(head_dir)  # walked once, shared by all deps
    results: list[DepResult] = []
    for dep in added[:MAX_AUDITED_DEPS]:
        results.append(audit_dep(dep, registry, head_dir, now=now, cache=cache))
    for dep in added[MAX_AUDITED_DEPS:]:
        results.append(DepResult(
            name=dep.name, ecosystem=dep.ecosystem, manifest=dep.manifest,
            version_spec=dep.version_spec, exists=None, age_days=None,
            weekly_downloads=None, typo_match=None, typo_distance=None,
            imported=None, verdicts=["NOT-AUDITED"],
            section=dep.section_hint,
            manifest_path=dep.manifest_path or dep.manifest))
    return results


def exit_code_for(results: list[DepResult]) -> int:
    return 1 if any(r.blocking for r in results) else 0


# ----------------------------------------------------------------- output ----
def _fmt(value, suffix=""):
    return "unknown" if value is None else f"{value}{suffix}"


def _esc_pipe(s: str) -> str:
    return str(s).replace("|", "\\|")


def render_text(results: list[DepResult], notes: list[str] | None = None) -> str:
    notes = notes or []
    if not results:
        out = "PhantomDep: no dependencies added. Nothing to audit.\n"
        return out + "".join(f"Note: {n}\n" for n in notes)
    skipped = [r for r in results if "SKIPPED-NONREGISTRY" in r.verdicts]
    not_audited = [r for r in results if "NOT-AUDITED" in r.verdicts]
    audited = [r for r in results if "SKIPPED-NONREGISTRY" not in r.verdicts
               and "NOT-AUDITED" not in r.verdicts]
    lines = [f"PhantomDep: {len(audited)} added dep(s) audited, "
             f"{len(skipped)} non-registry dep(s) skipped, "
             f"{len(not_audited)} not audited (cap)."]
    for n in notes:
        lines.append(f"Note: {n}")
    for r in audited:
        lines.append(
            f"- {r.name} ({r.ecosystem}, {r.manifest_path or r.manifest}"
            + (f", section: {r.section}" if r.section else "")
            + f"): {', '.join(r.verdicts)}"
            f" | exists={_fmt(r.exists)} age={_fmt(r.age_days, 'd')}"
            f" weekly_downloads={_fmt(r.weekly_downloads)}"
            f" imported={_fmt(r.imported)}"
            + (f" | typo-of: {r.typo_match} (distance {r.typo_distance})"
               if r.typo_match else ""))
    if skipped:
        lines.append("Skipped (non-registry specs — not audited against a "
                     "registry, no effect on exit code):")
        for r in skipped:
            lines.append(f"- {r.name} ({r.ecosystem}, "
                         f"{r.manifest_path or r.manifest}"
                         + (f", section: {r.section}" if r.section else "")
                         + f"): SKIPPED-NONREGISTRY spec={r.version_spec!r}")
    lines.append("Exit policy: 1 only if any NOT-FOUND or blocking "
                 "SUSPECT-TYPO; NEWBORN/LOW-DOWNLOADS/UNUSED/TYPO-LOOKALIKE "
                 "are warnings (exit 0).")
    return "\n".join(lines) + "\n"


def render_comment(results: list[DepResult], notes: list[str] | None = None) -> str:
    notes = notes or []
    lines = [COMMENT_MARKER, "## 👻 PhantomDep PR Gate", ""]
    if not results:
        lines.append("No dependencies were added in this PR. Nothing to audit.")
        for n in notes:
            lines.append(f"\n⚠️ **Audit note:** {n}")
        return "\n".join(lines) + "\n"
    skipped = [r for r in results if "SKIPPED-NONREGISTRY" in r.verdicts]
    audited = [r for r in results if "SKIPPED-NONREGISTRY" not in r.verdicts]
    if audited:
        lines += ["| Package | Ecosystem | Manifest | Section | Verdict | Age | Weekly downloads | Imported? |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in audited:
            lines.append(
                f"| `{_esc_pipe(r.name)}` | {r.ecosystem} | `{_esc_pipe(r.manifest_path or r.manifest)}` | {_esc_pipe(r.section or '—')} "
                f"| {', '.join(r.verdicts)} "
                + (f"(close to `{_esc_pipe(r.typo_match)}`)" if r.typo_match else "")
                + f"| {_fmt(r.age_days, ' days')} | {_fmt(r.weekly_downloads)} "
                  f"| {_fmt(r.imported)} |")
    blocking = [r.name for r in results if r.blocking]
    incomplete = any("NOT-AUDITED" in r.verdicts for r in results) or bool(notes)
    lines += [""]
    if blocking:
        lines.append(f"**Blocking:** {', '.join(blocking)} — NOT-FOUND or "
                     "blocking SUSPECT-TYPO. Do not merge until resolved.")
        if incomplete:
            lines.append("⚠️ **Audit incomplete** — some added deps were "
                         "NOT-AUDITED (cap) or a note below applies; the "
                         "blocking list above may not be complete.")
    elif incomplete:
        lines.append("⚠️ **Audit incomplete** — some added deps were "
                     "NOT-AUDITED (cap) or a note below applies; 'no "
                     "blocking findings' does NOT cover the unaudited rest.")
    else:
        lines.append("No blocking findings. Warnings above (NEWBORN / "
                     "LOW-DOWNLOADS / UNUSED / TYPO-LOOKALIKE) are advisory only.")
    for n in notes:
        lines.append(f"\n⚠️ **Audit note:** {n}")
    if skipped:
        lines += ["", "### Skipped — non-registry specs (not audited)", ""]
        for r in skipped:
            lines.append(f"- `{_esc_pipe(r.name)}` ({r.ecosystem}, "
                         f"section: {_esc_pipe(r.section or '—')}): "
                         f"`{_esc_pipe(r.version_spec)}`")
    lines.append("")
    lines.append("_PhantomDep audits only deps added by this PR. Existence "
                 "checks are not unique to this tool (see pkgguard-cli / "
                 "Socket); its delta is diff-scoping + import-usage + this "
                 "one review comment. Download counts are \"unknown\" where "
                 "no API provides them (PyPI counts in --live mode come "
                 "from pypistats)._")
    return "\n".join(lines) + "\n"


def results_to_json(results: list[DepResult], notes: list[str] | None = None) -> str:
    return json.dumps({"tool": "phantomdep", "version": VERSION,
                       "notes": notes or [],
                       "exit_code": exit_code_for(results),
                       "results": [asdict(r) | {"blocking": r.blocking}
                                   for r in results]},
                      indent=2, sort_keys=True) + "\n"


# -------------------------------------------------------------------- CLI ----
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="phantomdep",
                                 description="Audit only dependencies added in a PR diff.")
    ap.add_argument("--base-dir", help="directory with base manifests")
    ap.add_argument("--head-dir", help="directory with head manifests + sources")
    ap.add_argument("--base", help="git ref for base")
    ap.add_argument("--head", help="git ref for head")
    ap.add_argument("--diff-file", help="unified diff file")
    ap.add_argument("--repo", default=".", help="git repo dir (for --base/--head)")
    ap.add_argument("--live", action="store_true",
                    help="use live registries (default: offline — every dep "
                         "reports registry-unknown unless --registry-fixtures is given)")
    ap.add_argument("--registry-fixtures",
                    help="dir of captured registry JSON fixtures (offline mode)")
    ap.add_argument("--comment-out", help="write markdown PR comment to this file")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = ap.parse_args(argv)

    head_dir = args.head_dir
    added: list[AddedDep] | None = None
    try:
        if args.base_dir and args.head_dir:
            if not os.path.isdir(args.base_dir) or not os.path.isdir(args.head_dir):
                print("error: --base-dir/--head-dir must be directories", file=sys.stderr)
                return 2
            added = diff_manifests(
                load_manifests_from_dir(args.base_dir, collect_errors=True),
                load_manifests_from_dir(args.head_dir, collect_errors=True))
        elif args.diff_file:
            text = _read_capped(args.diff_file, MAX_DIFF_BYTES)
            if text is None:
                print("error: cannot read --diff-file (missing or oversized)",
                      file=sys.stderr)
                return 2
            added = parse_diff_file(text)
            head_dir = head_dir or (args.repo if os.path.isdir(args.repo) else None)
        elif args.base and args.head:
            added = diff_manifests(
                load_manifests_from_ref(args.base, args.repo, collect_errors=True),
                load_manifests_from_ref(args.head, args.repo, collect_errors=True))
            head_dir = head_dir or args.repo
        else:
            ap.print_usage(sys.stderr)
            print("error: give one input mode: --base-dir+--head-dir, "
                  "--diff-file, or --base+--head", file=sys.stderr)
            return 2
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.live:
        registry = LiveRegistry()
    elif args.registry_fixtures:
        try:
            registry = FakeRegistry.from_fixture_dir(args.registry_fixtures)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    else:
        registry = _UnknownRegistry()

    notes: list[str] = []
    if getattr(added, "manifest_truncated", False):
        notes.append(
            f"manifest cap: {added.manifest_total} manifests found, only "
            f"the first {added.manifest_audited} (sorted by path, same set "
            "on both sides) were audited; the rest were NOT audited.")
    results = audit(added, registry, head_dir)
    if any("NOT-AUDITED" in r.verdicts for r in results):
        notes.append(
            f"dep cap: more than {MAX_AUDITED_DEPS} added deps; the rest "
            "are marked NOT-AUDITED and were not checked.")
    if args.comment_out:
        with open(args.comment_out, "w", encoding="utf-8") as fh:
            fh.write(render_comment(results, notes=notes))
    sys.stdout.write(results_to_json(results, notes=notes) if args.json
                     else render_text(results, notes=notes))
    return exit_code_for(results)


class _UnknownRegistry:
    def lookup(self, ecosystem: str, name: str) -> RegistryInfo:
        return RegistryInfo(exists=None, error="offline-no-data")


if __name__ == "__main__":
    sys.exit(main())
