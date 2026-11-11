# Security

## What PhantomDep does with untrusted input

PhantomDep's inputs are all untrusted: PR manifests, diff files, source
trees, and **registry responses**. Controls:

- **Never executes scanned-repo code.** No `import`, `eval`, subprocess
  on repo content, or package installation — of the tool's own inputs or
  of any package named in a manifest — ever. The only subprocess is
  `git show <ref>:<manifest>` for the `--base/--head` mode, with the ref
  passed as a single argv element (no shell).
- **Size caps:** manifests 2 MB, diff files 5 MB, registry responses
  2 MB, source files scanned for imports 1 MB each. Oversized inputs are
  skipped/rejected, not parsed unboundedly.
- **Defensive registry parsing:** response JSON is type-checked field by
  field; malformed or hostile responses degrade that dep's fields to
  `unknown` instead of crashing or being trusted. Download counts are
  never invented — where the API provides none (PyPI JSON API), the
  value is `unknown`.
- **No credentials:** registry APIs used are public and keyless. The tool
  sends no auth headers (only a `User-Agent`), reads no tokens from the
  environment, and never prints tokens. The GitHub Action example uses
  only the workflow's default `GITHUB_TOKEN` with `pull-requests: write`
  for the one comment.
- **Determinism:** given the same registry data, output is identical.
  Live mode is opt-in via `--live`; the default and all tests are offline.

## Reporting a vulnerability

Open a private report to the maintainer of this repository. Do not file
public issues containing credentials or exploit details for live repos.
