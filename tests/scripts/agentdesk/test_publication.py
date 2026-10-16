"""Release ordering is independent of workflow queue completion order."""
from scripts.agentdesk.publication import eligible


def test_superseded_commit_cannot_publish_even_when_its_tests_pass():
    assert not eligible('old', 'new', 10, [])


def test_older_slow_completion_cannot_replace_newer_publication():
    assert not eligible('main', 'main', 10, [9, 11])
    assert eligible('main', 'main', 12, [9, 11])


def test_rerun_cannot_replace_published_artifacts():
    assert not eligible('main', 'main', 12, [12])
