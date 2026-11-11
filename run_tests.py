#!/usr/bin/env python3
"""Self-contained offline tests: python3 run_tests.py (zero deps, zero network).

Every audit in here uses FakeRegistry loaded from tests/fixtures/registry/.
No test touches LiveRegistry or the network.
"""
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phantomdep as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(ROOT, "fixtures")
REGDIR = os.path.join(ROOT, "tests", "fixtures", "registry")
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)  # fixed: deterministic ages


def registry():
    return pd.FakeRegistry.from_fixture_dir(REGDIR)


def run_pair(pair):
    base = os.path.join(FIX, pair, "base")
    head = os.path.join(FIX, pair, "head")
    added = pd.diff_manifests(pd.load_manifests_from_dir(base),
                              pd.load_manifests_from_dir(head))
    return added, pd.audit(added, registry(), head, now=NOW)


class CorpusTests(unittest.TestCase):
    def test_01_clean_npm_ok(self):
        added, results = run_pair("01-clean-npm")
        self.assertEqual([a.name for a in added], ["lodash"])
        self.assertEqual(results[0].verdicts, ["OK"])
        self.assertEqual(pd.exit_code_for(results), 0)

    def test_02_typo_pypi_not_found_and_suspect(self):
        added, results = run_pair("02-typo-pypi")
        self.assertEqual([a.name for a in added], ["reqeusts"])
        self.assertIn("NOT-FOUND", results[0].verdicts)
        self.assertIn("SUSPECT-TYPO", results[0].verdicts)
        self.assertEqual(results[0].typo_match, "requests")
        self.assertEqual(pd.exit_code_for(results), 1)

    def test_03_newborn_pypi(self):
        added, results = run_pair("03-newborn-pypi")
        self.assertEqual([a.name for a in added], ["freshpypi-lib"])
        self.assertIn("NEWBORN", results[0].verdicts)
        self.assertEqual(results[0].age_days, 3)
        self.assertNotIn("UNUSED", results[0].verdicts)
        self.assertEqual(pd.exit_code_for(results), 0)  # warning only

    def test_04_unused_npm(self):
        added, results = run_pair("04-unused-npm")
        self.assertEqual([a.name for a in added], ["axios"])
        self.assertIn("UNUSED", results[0].verdicts)
        self.assertEqual(pd.exit_code_for(results), 0)

    def test_05_scoped_npm(self):
        added, results = run_pair("05-scoped-npm")
        self.assertEqual([a.name for a in added], ["@babel/core"])
        self.assertEqual(results[0].verdicts, ["OK"])
        self.assertTrue(results[0].imported)

    def test_06_version_bump_ignored(self):
        added, results = run_pair("06-version-bump")
        self.assertEqual(added, [])
        self.assertEqual(results, [])

    def test_07_removed_ignored(self):
        added, _ = run_pair("07-removed")
        self.assertEqual(added, [])

    def test_08_devdeps_detected(self):
        added, results = run_pair("08-devdeps-npm")
        self.assertEqual([a.name for a in added], ["eslint"])
        self.assertEqual(results[0].verdicts, ["OK"])

    def test_09_pyproject_alias_and_optional_deps(self):
        added, results = run_pair("09-pyproject-alias")
        self.assertEqual({a.name for a in added}, {"scikit-learn", "requests"})
        for r in results:
            self.assertEqual(r.verdicts, ["OK"], r.name)
            self.assertTrue(r.imported, r.name)

    def test_10_requirements_clean(self):
        added, results = run_pair("10-requirements-clean")
        self.assertEqual([a.name for a in added], ["requests"])
        self.assertEqual(results[0].verdicts, ["OK"])
        # PyPI downloads are honestly unknown, never fabricated
        self.assertIsNone(results[0].weekly_downloads)

    def test_12_typo_lookalike_preact_no_block(self):
        added, results = run_pair("12-typo-lookalike-npm")
        self.assertEqual([a.name for a in added], ["preact"])
        self.assertIn("TYPO-LOOKALIKE", results[0].verdicts)
        self.assertNotIn("SUSPECT-TYPO", results[0].verdicts)
        self.assertEqual(pd.exit_code_for(results), 0)

    def test_13_peer_to_deps_move_not_addition(self):
        added, _ = run_pair("13-peer-move-npm")
        self.assertEqual(added, [])

    def test_14_nonregistry_workspace_skipped(self):
        added, results = run_pair("14-nonregistry-npm")
        self.assertEqual([a.name for a in added], ["local-lib"])
        self.assertEqual(results[0].verdicts, ["SKIPPED-NONREGISTRY"])
        self.assertEqual(pd.exit_code_for(results), 0)

    def test_15_scoped_clean_no_typo(self):
        added, results = run_pair("15-scoped-clean-npm")
        self.assertEqual([a.name for a in added], ["@angular/core"])
        self.assertEqual(results[0].verdicts, ["OK"])
        self.assertEqual(pd.exit_code_for(results), 0)

    def test_11_newborn_npm_low_downloads(self):
        added, results = run_pair("11-newborn-npm")
        self.assertIn("NEWBORN", results[0].verdicts)
        self.assertIn("LOW-DOWNLOADS", results[0].verdicts)
        self.assertEqual(pd.exit_code_for(results), 0)


class UnitTests(unittest.TestCase):
    def test_levenshtein(self):
        self.assertEqual(pd.levenshtein("requests", "requests"), 0)
        self.assertEqual(pd.levenshtein("reqeusts", "requests"), 2)
        self.assertEqual(pd.levenshtein("lodahs", "lodash"), 2)
        self.assertEqual(pd.levenshtein("", "abc"), 3)

    def test_popular_itself_not_typo(self):
        self.assertIsNone(pd.closest_popular("requests", "pypi"))
        self.assertIsNone(pd.closest_popular("lodash", "npm"))
        self.assertIsNone(pd.closest_popular("@babel/core", "npm"))

    def test_parse_requirements_variants(self):
        deps = pd.parse_requirements(
            "requests>=2.31\nflask[async]==3.0; python_version>'3.8'\n"
            "# comment\n-r other.txt\nnumpy\n")
        self.assertEqual(set(deps), {"requests", "flask", "numpy"})

    def test_parse_pyproject_string_fallback_shapes(self):
        deps = pd.parse_pyproject(
            '[project]\nname="x"\ndependencies=["requests>=2.31"]\n'
            '[project.optional-dependencies]\ndev=["pytest>=8"]\n')
        self.assertIn("requests", deps)
        self.assertIn("pytest", deps)

    def test_unknown_alias_is_unknown_not_unused(self):
        dep = pd.AddedDep(name="some-hyphen-lib", ecosystem="pypi",
                          manifest="requirements.txt")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w") as fh:
                fh.write("print(1)\n")
            self.assertIsNone(pd.check_imported(dep, d))

    def test_diff_file_mode(self):
        diff = (
            "diff --git a/requirements.txt b/requirements.txt\n"
            "--- a/requirements.txt\n+++ b/requirements.txt\n"
            "@@ -1 +1,2 @@\n flask==3.0.0\n+reqeusts==2.31.0\n")
        added = pd.parse_diff_file(diff)
        self.assertEqual([a.name for a in added], ["reqeusts"])

    def test_diff_file_version_bump_cancels(self):
        diff = (
            "diff --git a/package.json b/package.json\n"
            "--- a/package.json\n+++ b/package.json\n"
            '@@ -1 +1 @@\n-    "lodash": "^4.17.20"\n+    "lodash": "^4.17.21"\n')
        self.assertEqual(pd.parse_diff_file(diff), [])

    def test_diff_file_single_line_package_json(self):
        diff = (
            "diff --git a/package.json b/package.json\n"
            "--- a/package.json\n+++ b/package.json\n"
            '@@ -1 +1 @@\n-{"name":"x","dependencies":{}}\n'
            '+{"name":"x","dependencies":{"lodash":"^4.17.21"}}\n')
        added = pd.parse_diff_file(diff)
        self.assertEqual([a.name for a in added], ["lodash"])

    def test_fake_registry_missing_is_not_found(self):
        info = registry().lookup("pypi", "definitely-not-in-fixtures-zzz")
        self.assertFalse(info.exists)

    def test_render_comment_and_json(self):
        _, results = run_pair("02-typo-pypi")
        md = pd.render_comment(results)
        self.assertIn("reqeusts", md)
        self.assertIn("Blocking", md)
        data = json.loads(pd.results_to_json(results))
        self.assertEqual(data["exit_code"], 1)

    def test_no_network_guard(self):
        # FakeRegistry has no socket/urllib surface; LiveRegistry never used here.
        self.assertFalse(hasattr(registry(), "_get_json"))


class CliTests(unittest.TestCase):
    def _cli(self, *argv):
        return subprocess.run([sys.executable, os.path.join(ROOT, "phantomdep.py"), *argv],
                              capture_output=True, text=True, timeout=30)

    def test_cli_clean_pair_exit_0(self):
        r = self._cli("--base-dir", os.path.join(FIX, "01-clean-npm", "base"),
                      "--head-dir", os.path.join(FIX, "01-clean-npm", "head"),
                      "--registry-fixtures", REGDIR)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lodash", r.stdout)

    def test_cli_typo_pair_exit_1_and_comment(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comment = os.path.join(d, "comment.md")
            r = self._cli("--base-dir", os.path.join(FIX, "02-typo-pypi", "base"),
                          "--head-dir", os.path.join(FIX, "02-typo-pypi", "head"),
                          "--registry-fixtures", REGDIR,
                          "--comment-out", comment)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            with open(comment) as fh:
                self.assertIn("PhantomDep PR Gate", fh.read())

    def test_cli_json(self):
        r = self._cli("--base-dir", os.path.join(FIX, "10-requirements-clean", "base"),
                      "--head-dir", os.path.join(FIX, "10-requirements-clean", "head"),
                      "--registry-fixtures", REGDIR, "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["results"][0]["name"], "requests")

    def test_cli_no_mode_is_usage_error(self):
        self.assertEqual(self._cli().returncode, 2)


def _audit_one(name, eco, info, spec="^1.0.0", head=None):
    reg = pd.FakeRegistry({(eco, name): info})
    dep = pd.AddedDep(name=name, ecosystem=eco, manifest="package.json"
                      if eco == "npm" else "requirements.txt",
                      version_spec=spec)
    return pd.audit_dep(dep, reg, head, now=NOW)


OLD = "2015-01-01T00:00:00.000Z"
YOUNG = "2026-11-05T00:00:00.000Z"


class TypoGuardTests(unittest.TestCase):
    def test_established_lookalikes_never_block(self):
        cases = [("preact", "npm", 3_000_000), ("ms", "npm", 90_000_000),
                 ("pug", "npm", 1_200_000), ("uid", "npm", 400_000),
                 ("numba", "pypi", None), ("sympy", "pypi", None),
                 ("black", "pypi", None)]
        for name, eco, dl in cases:
            r = _audit_one(name, eco, pd.RegistryInfo(
                exists=True, created=OLD, weekly_downloads=dl))
            self.assertIn("TYPO-LOOKALIKE", r.verdicts, name)
            self.assertFalse(r.blocking, name)
            self.assertEqual(pd.exit_code_for([r]), 0, name)

    def test_old_age_alone_exonerates_npm_low_downloads(self):
        r = _audit_one("preact", "npm", pd.RegistryInfo(
            exists=True, created=OLD, weekly_downloads=5))
        self.assertIn("TYPO-LOOKALIKE", r.verdicts)

    def test_not_found_still_blocks(self):
        r = _audit_one("reqeusts", "pypi", pd.RegistryInfo(exists=False),
                       spec="==2.31.0")
        self.assertIn("NOT-FOUND", r.verdicts)
        self.assertIn("SUSPECT-TYPO", r.verdicts)
        self.assertEqual(pd.exit_code_for([r]), 1)

    def test_young_low_download_near_name_blocks(self):
        r = _audit_one("lodahs", "npm", pd.RegistryInfo(
            exists=True, created=YOUNG, weekly_downloads=3))
        self.assertIn("SUSPECT-TYPO", r.verdicts)
        self.assertEqual(pd.exit_code_for([r]), 1)

    def test_young_pypi_near_name_blocks(self):
        r = _audit_one("requets", "pypi", pd.RegistryInfo(
            exists=True, created=YOUNG, weekly_downloads=None), spec="==1.0")
        self.assertIn("SUSPECT-TYPO", r.verdicts)
        self.assertTrue(r.blocking)

    def test_unknown_registry_typo_is_warning_only(self):
        dep = pd.AddedDep(name="reqeusts", ecosystem="pypi",
                          manifest="requirements.txt", version_spec="==2.31.0")
        r = pd.audit_dep(dep, pd._UnknownRegistry(), None, now=NOW)
        self.assertIn("TYPO-LOOKALIKE", r.verdicts)
        self.assertIn("UNKNOWN-REGISTRY", r.verdicts)
        self.assertEqual(pd.exit_code_for([r]), 0)

    def test_scoped_compare_same_form_only(self):
        for name in ("@angular/core", "@nestjs/core", "@vue/cli"):
            self.assertIsNone(pd.closest_popular(name, "npm"), name)
            r = _audit_one(name, "npm", pd.RegistryInfo(
                exists=True, created=OLD, weekly_downloads=1_000_000))
            self.assertNotIn("SUSPECT-TYPO", r.verdicts, name)
            self.assertNotIn("TYPO-LOOKALIKE", r.verdicts, name)
        # scoped near a scoped popular still matches
        self.assertEqual(pd.closest_popular("@babel/cor", "npm"),
                         ("@babel/core", 1))
        # bare names of scoped populars are not popular themselves
        self.assertNotEqual((pd.closest_popular("core", "npm") or ("",))[0],
                            "@babel/core")


class NonRegistryTests(unittest.TestCase):
    def test_all_spec_forms_skipped(self):
        forms = ["workspace:*", "workspace:^1.0.0", "file:../local",
                 "link:../local", "git+https://github.com/x/y.git",
                 "git://github.com/x/y.git", "https://example.com/y.tgz",
                 "http://example.com/y.tgz", "npm:lodash@^4.17.21",
                 "catalog:", "portal:../y"]
        for spec in forms:
            r = _audit_one("some-lib", "npm", pd.RegistryInfo(exists=False),
                           spec=spec)
            self.assertEqual(r.verdicts, ["SKIPPED-NONREGISTRY"], spec)
            self.assertEqual(pd.exit_code_for([r]), 0, spec)

    def test_pep508_direct_reference_skipped(self):
        spec = "mypkg @ https://example.com/mypkg-1.0-py3-none-any.whl"
        deps = pd.parse_requirements(spec + "\n")
        self.assertIn("mypkg", deps)
        r = _audit_one("mypkg", "pypi", pd.RegistryInfo(exists=False),
                       spec=spec)
        self.assertEqual(r.verdicts, ["SKIPPED-NONREGISTRY"])

    def test_is_nonregistry_negative(self):
        for spec in ("^4.17.21", ">=2.31", "==1.0", "latest", "~1.2.3"):
            self.assertFalse(pd.is_nonregistry_spec(spec), spec)


class DiffModeTests(unittest.TestCase):
    def test_script_and_engines_lines_never_parse(self):
        diff = (
            "diff --git a/package.json b/package.json\n"
            "--- a/package.json\n+++ b/package.json\n"
            "@@ -1,4 +1,6 @@\n"
            '   "scripts": {\n'
            '-    "build": "tsc"\n'
            '+    "build:prod": "vite build",\n'
            '+    "lodash": "echo not-a-dep"\n'
            "   },\n"
            '   "engines": {\n'
            '+    "node": ">=18"\n'
            "   }\n")
        self.assertEqual(pd.parse_diff_file(diff), [])

    def test_dep_section_line_parses_with_section(self):
        diff = (
            "diff --git a/package.json b/package.json\n"
            "--- a/package.json\n+++ b/package.json\n"
            "@@ -1,3 +1,4 @@\n"
            '   "dependencies": {\n'
            '     "express": "^4.0.0",\n'
            '+    "lodash": "^4.17.21"\n'
            "   }\n")
        added = pd.parse_diff_file(diff)
        self.assertEqual([a.name for a in added], ["lodash"])
        self.assertEqual(added[0].section_hint, "dependencies")

    def test_peer_section_line_parses(self):
        diff = (
            "diff --git a/package.json b/package.json\n"
            "--- a/package.json\n+++ b/package.json\n"
            "@@ -1,2 +1,3 @@\n"
            '   "peerDependencies": {\n'
            '+    "react": "^18.0.0"\n'
            "   }\n")
        added = pd.parse_diff_file(diff)
        self.assertEqual([(a.name, a.section_hint) for a in added],
                         [("react", "peerDependencies")])

    def test_monorepo_same_name_add_remove_do_not_cancel(self):
        diff = (
            "diff --git a/packages/a/package.json b/packages/a/package.json\n"
            "--- a/packages/a/package.json\n+++ b/packages/a/package.json\n"
            "@@ -1,3 +1,4 @@\n"
            '   "dependencies": {\n'
            '+    "lodash": "^4.17.21"\n'
            "   }\n"
            "diff --git a/packages/b/package.json b/packages/b/package.json\n"
            "--- a/packages/b/package.json\n+++ b/packages/b/package.json\n"
            "@@ -1,3 +1,2 @@\n"
            '   "dependencies": {\n'
            '-    "lodash": "^4.17.20"\n'
            "   }\n")
        added = pd.parse_diff_file(diff)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].manifest_path, "packages/a/package.json")

    def test_pypi_case_insensitive_bump_cancels(self):
        diff = (
            "diff --git a/requirements.txt b/requirements.txt\n"
            "--- a/requirements.txt\n+++ b/requirements.txt\n"
            "@@ -1 +1 @@\n-Requests==2.0.0\n+requests==2.1.0\n")
        self.assertEqual(pd.parse_diff_file(diff), [])

    def test_pyproject_diff_only_inside_dependencies_array(self):
        diff = (
            "diff --git a/pyproject.toml b/pyproject.toml\n"
            "--- a/pyproject.toml\n+++ b/pyproject.toml\n"
            "@@ -1,3 +1,4 @@\n"
            " [project]\n"
            '+description = "requests are great"\n'
            ' dependencies = [\n'
            '+    "flask>=3",\n'
            " ]\n")
        added = pd.parse_diff_file(diff)
        self.assertEqual([a.name for a in added], ["flask"])
        self.assertEqual(added[0].section_hint, "project.dependencies")


class SectionTests(unittest.TestCase):
    def test_package_json_all_sections_and_hint(self):
        deps = pd._parse_package_json_entries(
            '{"dependencies": {"a": "1"}, "devDependencies": {"b": "1"},'
            ' "peerDependencies": {"c": "1"}, "optionalDependencies": {"d": "1"}}')
        self.assertEqual({k: v.section for k, v in deps.items()}, {
            "a": "dependencies", "b": "devDependencies",
            "c": "peerDependencies", "d": "optionalDependencies"})

    def test_pyproject_dependency_groups_and_poetry(self):
        text = (
            '[project]\nname="x"\ndependencies=["requests>=2"]\n'
            '[dependency-groups]\ndev=["pytest>=8"]\n'
            '[tool.poetry.dependencies]\npython="^3.11"\nclick="^8"\n')
        deps = pd.parse_pyproject(text)
        self.assertEqual(set(deps), {"requests", "pytest", "click"})
        entries = pd._parse_pyproject_entries(text)
        self.assertEqual(entries["pytest"].section, "dependency-groups.dev")
        self.assertEqual(entries["click"].section, "tool.poetry.dependencies")

    def test_section_shown_in_outputs(self):
        _, results = run_pair("01-clean-npm")
        self.assertEqual(results[0].section, "dependencies")
        self.assertIn("section: dependencies", pd.render_text(results))
        self.assertIn("dependencies", pd.render_comment(results))


class SilentPassTests(unittest.TestCase):
    def _cli(self, *argv):
        return subprocess.run([sys.executable,
                               os.path.join(ROOT, "phantomdep.py"), *argv],
                              capture_output=True, text=True, timeout=30)

    def test_unparseable_head_manifest_exit_2_names_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base, head = os.path.join(d, "b"), os.path.join(d, "h")
            os.makedirs(base); os.makedirs(head)
            with open(os.path.join(base, "package.json"), "w") as fh:
                fh.write('{"dependencies": {}}')
            with open(os.path.join(head, "package.json"), "w") as fh:
                fh.write('{not json')
            r = self._cli("--base-dir", base, "--head-dir", head,
                          "--registry-fixtures", REGDIR)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("package.json", r.stderr)

    def test_invalid_toml_exit_2_not_fallback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "pyproject.toml"), "w") as fh:
                fh.write('[project\ndependencies=["requests"]\n')
            with self.assertRaises(pd.ManifestError):
                pd.load_manifests_from_dir(d)

    def test_oversized_manifest_exit_2(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base, head = os.path.join(d, "b"), os.path.join(d, "h")
            os.makedirs(base); os.makedirs(head)
            with open(os.path.join(head, "package.json"), "wb") as fh:
                fh.write(b'{"dependencies": {}}' + b" " * (pd.MAX_MANIFEST_BYTES + 1))
            r = self._cli("--base-dir", base, "--head-dir", head,
                          "--registry-fixtures", REGDIR)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("oversized", r.stderr)

    def test_fallback_parser_scoped_to_dep_arrays(self):
        text = ('[project]\nname = "x"\ndescription = "uses requests heavily"\n'
                'dependencies = ["flask>=3"]\n')
        entries = pd._fallback_pyproject_entries(text)
        self.assertEqual(set(entries), {"flask"})

    def test_git_mode_absent_manifest_ok_bad_ref_exit_2(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                subprocess.run(["git", *a], cwd=d, check=True,
                               capture_output=True, text=True)
            git("init", "-q")
            git("config", "user.email", "t@t.t"); git("config", "user.name", "t")
            with open(os.path.join(d, "README.md"), "w") as fh:
                fh.write("x\n")
            git("add", "."); git("commit", "-qm", "one")
            with open(os.path.join(d, "requirements.txt"), "w") as fh:
                fh.write("requests==2.32.3\n")
            with open(os.path.join(d, "app.py"), "w") as fh:
                fh.write("import requests\n")
            git("add", "."); git("commit", "-qm", "two")
            r = self._cli("--base", "HEAD~1", "--head", "HEAD", "--repo", d,
                          "--registry-fixtures", REGDIR)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("requests", r.stdout)
            r2 = self._cli("--base", "no-such-ref-zzz", "--head", "HEAD",
                           "--repo", d, "--registry-fixtures", REGDIR)
            self.assertEqual(r2.returncode, 2, r2.stdout + r2.stderr)


class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()
    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class LiveRegistryParseTests(unittest.TestCase):
    RAW = os.path.join(ROOT, "tests", "fixtures", "registry-raw")

    def _load(self, name):
        with open(os.path.join(self.RAW, name)) as fh:
            return json.load(fh)

    def _mock(self, router, seen):
        import unittest.mock as mock
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            seen.append(url)
            return _FakeResp(router(url))
        return mock.patch.object(pd.urllib.request, "urlopen", fake_urlopen)

    def test_npm_real_shape_and_downloads(self):
        npm, dl = self._load("npm-lodash.json"), self._load("npm-downloads-lodash.json")
        seen = []
        with self._mock(lambda u: dl if "downloads" in u else npm, seen):
            info = pd.LiveRegistry().lookup("npm", "lodash")
        self.assertTrue(info.exists)
        self.assertEqual(info.created, "2012-04-23T16:58:13.241Z")
        self.assertEqual(info.weekly_downloads, 55000000)

    def test_scoped_npm_url_percent2f(self):
        npm = dict(self._load("npm-lodash.json"), name="@babel/core")
        seen = []
        with self._mock(lambda u: {"downloads": 1} if "downloads" in u else npm, seen):
            pd.LiveRegistry().lookup("npm", "@babel/core")
        self.assertTrue(any("%2F" in u for u in seen), seen)

    def test_bool_downloads_rejected(self):
        npm = self._load("npm-lodash.json")
        with self._mock(lambda u: {"downloads": True} if "downloads" in u else npm, []):
            info = pd.LiveRegistry().lookup("npm", "lodash")
        self.assertIsNone(info.weekly_downloads)

    def test_malformed_npm_shape_is_unknown(self):
        with self._mock(lambda u: {"unexpected": 1}, []):
            info = pd.LiveRegistry().lookup("npm", "lodash")
        self.assertIsNone(info.exists)

    def test_pypi_yanked_skipped_and_upload_time_fallback(self):
        payload = self._load("pypi-requests.json")
        with self._mock(lambda u: payload, []):
            info = pd.LiveRegistry().lookup("pypi", "requests")
        self.assertTrue(info.exists)
        # 2010 file is yanked; earliest real file has only upload_time
        self.assertEqual(info.created, "2011-02-13T00:00:00")
        self.assertIsNone(info.weekly_downloads)

    def test_negative_age_is_unknown(self):
        r = _audit_one("freshpypi-lib", "pypi", pd.RegistryInfo(
            exists=True, created="2027-01-01T00:00:00.000Z"), spec=">=0.1")
        self.assertIsNone(r.age_days)
        self.assertNotIn("NEWBORN", r.verdicts)


class ImportV2Tests(unittest.TestCase):
    def _check(self, dep_name, eco, files):
        import tempfile
        d = tempfile.mkdtemp()
        for rel, content in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(content)
        dep = pd.AddedDep(name=dep_name, ecosystem=eco,
                          manifest="package.json" if eco == "npm" else "requirements.txt")
        return pd.check_imported(dep, d)

    def test_export_from(self):
        self.assertTrue(self._check("axios", "npm",
                                    {"a.js": "export { x } from 'axios';\n"}))

    def test_python_comma_and_semicolon_imports(self):
        self.assertTrue(self._check("requests", "pypi",
                                    {"a.py": "import os, requests\n"}))
        self.assertTrue(self._check("requests", "pypi",
                                    {"a.py": "x = 1; import requests\n"}))

    def test_dunder_import_and_importlib(self):
        self.assertTrue(self._check("requests", "pypi",
                                    {"a.py": "m = __import__('requests')\n"}))
        self.assertTrue(self._check(
            "requests", "pypi",
            {"a.py": "import importlib\nm = importlib.import_module('requests')\n"}))

    def test_new_extensions(self):
        self.assertTrue(self._check("requests", "pypi",
                                    {"a.pyi": "import requests\n"}))
        self.assertTrue(self._check("axios", "npm",
                                    {"a.mts": "import axios from 'axios';\n"}))
        self.assertTrue(self._check("requests", "pypi",
                                    {"n.ipynb": '{"cells": [{"source": ["import requests"]}]}'}))

    def test_config_reference_no_unused(self):
        self.assertEqual(self._check(
            "axios", "npm",
            {"tailwind.config.js": "module.exports = { plugins: ['axios'] };\n",
             "src/index.js": "console.log(1);\n"}), "referenced")
        self.assertEqual(self._check(
            "axios", "npm",
            {"package.json": '{"scripts": {"go": "axios --help"}}'}), "referenced")

    def test_manifest_self_listing_is_not_a_reference(self):
        self.assertFalse(self._check(
            "axios", "npm",
            {"package.json": '{"dependencies": {"axios": "^1.0.0"}}',
             "src/index.js": "console.log(1);\n"}))

    def test_new_aliases(self):
        cases = [("mysqlclient", "import MySQLdb\n"),
                 ("djangorestframework", "import rest_framework\n"),
                 ("GitPython", "import git\n"),
                 ("pyserial", "import serial\n"),
                 ("protobuf", "import google.protobuf\n"),
                 ("attrs", "import attr\n"),
                 ("opencv-python", "import cv2\n")]
        for name, src in cases:
            self.assertTrue(self._check(name, "pypi", {"a.py": src}), name)

    def test_symlink_escape_not_followed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as outside, \
                tempfile.TemporaryDirectory() as root:
            secret = os.path.join(outside, "evil.py")
            with open(secret, "w") as fh:
                fh.write("import requests\n")
            os.symlink(secret, os.path.join(root, "linked.py"))
            dep = pd.AddedDep(name="requests", ecosystem="pypi",
                              manifest="requirements.txt")
            self.assertFalse(pd.check_imported(dep, root))

    def test_audit_cap_200(self):
        deps = [pd.AddedDep(name=f"pkg-{i:03d}", ecosystem="npm",
                            manifest="package.json", version_spec="^1.0.0")
                for i in range(205)]
        results = pd.audit(deps, pd.FakeRegistry({}), None, now=NOW)
        self.assertEqual(len(results), 205)
        self.assertEqual([r.verdicts for r in results[200:]],
                         [["NOT-AUDITED"]] * 5)


class MiscFixTests(unittest.TestCase):
    def test_invalid_dep_names_filtered(self):
        self.assertIsNone(pd.parse_requirement_name("./localpkg"))
        self.assertIsNone(pd.parse_requirement_name("-"))
        deps = pd.parse_requirements("./localpkg\nrequests>=2\n")
        self.assertEqual(set(deps), {"requests"})

    def test_comment_marker_and_pipe_escaping(self):
        r = pd.DepResult(name="a|b", ecosystem="npm", manifest="package.json",
                         version_spec="1", exists=True, age_days=10,
                         weekly_downloads=None, typo_match=None,
                         typo_distance=None, imported=True, verdicts=["OK"])
        md = pd.render_comment([r])
        self.assertTrue(md.startswith(pd.COMMENT_MARKER))
        self.assertIn("a\\|b", md)

    def test_malformed_fixture_is_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "bad.json"), "w") as fh:
                json.dump({"ecosystem": "npm", "name": "x", "exists": "yes"}, fh)
            with self.assertRaises(ValueError):
                pd.FakeRegistry.from_fixture_dir(d)
            with open(os.path.join(d, "bad.json"), "w") as fh:
                json.dump({"ecosystem": "npm", "name": "x", "exists": True,
                           "weekly_downloads": True}, fh)
            with self.assertRaises(ValueError):
                pd.FakeRegistry.from_fixture_dir(d)

    def test_popular_list_refresh(self):
        for n in ("dayjs", "nanoid", "three"):
            self.assertIn(n, pd.POPULAR_NPM)
            self.assertIsNone(pd.closest_popular(n, "npm"))
        for n in ("typing-extensions", "packaging", "rich"):
            self.assertIn(n, pd.POPULAR_PYPI)
        self.assertNotIn("pip", pd.POPULAR_PYPI)
        self.assertNotIn("wheel", pd.POPULAR_PYPI)

    def test_action_yml_contract(self):
        with open(os.path.join(ROOT, "action.yml")) as fh:
            yml = fh.read()
        self.assertIn("if: always()", yml)
        self.assertIn('!= "0"', yml)              # enforce fails on ANY non-zero
        self.assertIn("paginate", yml)
        self.assertIn("per_page: 100", yml)
        self.assertIn("<!-- phantomdep-pr-gate -->", yml)
        self.assertIn("existsSync", yml)          # missing comment tolerated
        self.assertIn(".includes(marker)", yml)   # marker anywhere in body


if __name__ == "__main__":
    unittest.main(verbosity=2)
