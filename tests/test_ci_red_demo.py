# S4.5 evidence only: proves a failing test turns CI red and blocks the merge.
# This branch is never merged.


def test_ci_blocks_merge_on_failure():
    assert False, "intentional failure to demonstrate merge blocking"
