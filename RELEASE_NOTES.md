# Release record

Both releases below are **complete**: the repo is pushed, both tags
were created as GitHub releases, and consumers can pin either tag.

## v0.1 — released 2026-11-10

- [x] Confirm the v0.1 tree is the pushed tree (76/76 files, head eceb988).
- [x] Create tag `v0.1` pointing at that v0.1 head commit `eceb988` —
      it does NOT point at a v0.2 commit. (Created before the v0.2
      upload, while the head was still `eceb988`.)
- [x] Push only that tag, after explicit approval.
- [x] Verify consumers can pin: `uses: RakTikoo/phantomdep-pr-gate@v0.1`
- [x] Verify the tag is visible on the repo's Tags / Releases view:
      https://github.com/RakTikoo/phantomdep-pr-gate/releases/tag/v0.1

## v0.2 — released 2026-11-10

- [x] After Rakshit approved the v0.2 push, it was verified
      file-by-file (107/107 files in the recursive tree, head
      `97009c0`, 20 commits added over v0.1).
- [x] Tag the v0.2 head commit as `v0.2`, following the same pattern:
      https://github.com/RakTikoo/phantomdep-pr-gate/releases/tag/v0.2
- [x] Consumers may pin `uses: RakTikoo/phantomdep-pr-gate@v0.2`.
- [x] The repo's own CI ran green on GitHub for the first time on
      this head (tests run #20, success on Python 3.11 and 3.12,
      108 tests).

## Live cross-repo proof — 2026-11-11

- [x] The published Action (`@v0.2`) ran in another repo,
      [RakTikoo/phantomdep-dogfood](https://github.com/RakTikoo/phantomdep-dogfood):
      clean PR #1 green with one OK comment (updated in place by a
      bump commit, still one comment); bad PR #2 (`reqeusts`) blocked
      red with a NOT-FOUND + SUSPECT-TYPO comment. Both PRs are open
      as evidence.
