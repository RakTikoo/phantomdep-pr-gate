<!-- phantomdep-pr-gate -->
## 👻 PhantomDep PR Gate

| Package | Ecosystem | Manifest | Section | Verdict | Age | Weekly downloads | Imported? |
|---|---|---|---|---|---|---|---|
| `axios` | npm | `package.json` | dependencies | UNUSED | 4402 days | 48000000 | False |
| `freshpypi-lib` | pypi | `requirements.txt` | requirements | NEWBORN | 3 days | unknown | True |
| `reqeusts` | pypi | `requirements.txt` | requirements | NOT-FOUND, SUSPECT-TYPO, UNUSED (close to `requests`)| unknown | unknown | False |

**Blocking:** reqeusts — NOT-FOUND or blocking SUSPECT-TYPO. Do not merge until resolved.

_PhantomDep audits only deps added by this PR. Existence checks are not unique to this tool (see pkgguard-cli / Socket); its delta is diff-scoping + import-usage + this one review comment. Download counts are "unknown" where no API provides them (PyPI counts in --live mode come from pypistats)._
