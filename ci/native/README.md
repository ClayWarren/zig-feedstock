# Fork-only acceptance harness

This branch is not an upstream PR candidate. Its parent is the immutable recipe
candidate. The inherited generated matrix is removed only from this disposable
CI branch so the fork runs two focused native jobs; the recipe candidate retains
the untouched conda-smithy-generated workflow.

Both jobs install into fresh conda-forge-only Windows ARM64 environments with
cache restoration disabled. Protobuf pins the published build-3 artifact and
checks native upb plus serialization behavior. Zig builds all four outputs and
runs the full recipe test suite without skipping tests or publishing packages.

The Zig official-seed compiler-patch limitation and build-number reconciliation
with upstream PR 176 still require review before upstream submission.
