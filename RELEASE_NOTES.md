# Release / tagging checklist

## v0.1 (already pushed, tag prep only — no tag created by the v0.2 build)

On the eventual approved push / release step:

- [ ] Confirm the v0.1 tree is the pushed tree (76/76 files, head eceb988).
- [ ] Create an annotated tag `v0.1` pointing at that v0.1 head commit —
      do NOT point v0.1 at a v0.2 commit.
- [ ] Push only that tag, after explicit approval.
- [ ] Verify consumers can pin: `uses: RakTikoo/phantomdep-pr-gate@v0.1`
- [ ] Verify the tag is visible on the repo's Tags / Releases view.

## v0.2 (this tree, not pushed)

- [ ] After Rakshit approves the v0.2 push and it is verified file-by-file,
      tag the v0.2 head commit as `v0.2` following the same pattern.
- [ ] Consumers may then pin `uses: RakTikoo/phantomdep-pr-gate@v0.2`.

No tag or repo was created or pushed by the local v0.2 build.
