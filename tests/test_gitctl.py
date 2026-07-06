import subprocess

import pytest

from loopwright.gitctl.repo import BRANCHES, GitError, ProjectRepo

PACKET = {
    "docs/project/DESIGN.md": "# Design\n",
    "docs/project/DEVPLAN.md": "# Devplan\n",
    "docs/project/TESTPLAN.md": "# Testplan\n",
}


@pytest.fixture
def repo(tmp_path):
    return ProjectRepo.init(tmp_path / "demo.git", PACKET)


def test_init_creates_bare_repo_with_all_branches(repo):
    assert repo.branches() == sorted(BRANCHES)


def test_all_branches_start_at_the_packet_commit(repo):
    heads = {branch: repo.head_of(branch) for branch in BRANCHES}
    assert len(set(heads.values())) == 1


def test_packet_files_are_on_design_main(repo):
    out = subprocess.run(
        ["git", "-C", str(repo.path), "ls-tree", "-r", "--name-only", "design/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert sorted(out.split()) == sorted(PACKET)


def test_init_refuses_existing_path(tmp_path, repo):
    with pytest.raises(GitError, match="already exists"):
        ProjectRepo.init(repo.path, PACKET)


def test_open_nonexistent_repo_raises(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        ProjectRepo(tmp_path / "nope.git")


def test_commit_packet_advances_design_main_only(repo):
    before = {branch: repo.head_of(branch) for branch in BRANCHES}
    new_hash = repo.commit_packet({"docs/project/DESIGN.md": "# Design v2\n"})
    assert repo.head_of("design/main") == new_hash
    assert new_hash != before["design/main"]
    for branch in BRANCHES:
        if branch != "design/main":
            assert repo.head_of(branch) == before[branch]


def test_commit_packet_rejects_escaping_paths(repo):
    with pytest.raises(ValueError, match="escapes repository"):
        repo.commit_packet({"../outside.md": "nope"})


def test_commit_packet_rejects_empty(repo):
    with pytest.raises(ValueError, match="no files"):
        repo.commit_packet({})


def test_checkpoint_tags_autoincrement_and_list(repo):
    first = repo.tag_checkpoint("bootstrap")
    second = repo.tag_checkpoint("core-working")
    assert first == "checkpoint/0001-bootstrap"
    assert second == "checkpoint/0002-core-working"
    assert repo.checkpoints() == [first, second]
    assert repo.next_checkpoint_number() == 3


def test_checkpoint_tag_points_at_requested_ref(repo):
    repo.commit_packet({"docs/project/DESIGN.md": "# v2\n"})
    tag = repo.tag_checkpoint("design-approved", ref="design/main")
    tag_target = subprocess.run(
        ["git", "-C", str(repo.path), "rev-parse", f"{tag}^{{commit}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert tag_target == repo.head_of("design/main")


def test_checkpoint_rejects_bad_slug(repo):
    for bad in ["", "UPPER", "has space", "-lead", "under_score"]:
        with pytest.raises(ValueError):
            repo.tag_checkpoint(bad)


def test_duplicate_slug_gets_new_number(repo):
    assert repo.tag_checkpoint("retry") == "checkpoint/0001-retry"
    assert repo.tag_checkpoint("retry") == "checkpoint/0002-retry"


def test_reopen_existing_repo(tmp_path, repo):
    reopened = ProjectRepo(repo.path)
    assert reopened.branches() == sorted(BRANCHES)
