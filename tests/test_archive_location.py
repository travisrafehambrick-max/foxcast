"""Where raw sources live (decided 2026-11-12, plan 1A.6).

Raw payloads stay on local disk, content-addressed by hash, outside any git repository.
Only a hash index (and the hashes facts already carry) may go into a data repo.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from foxcast.config import Settings, default_home, load_settings, write_default_config
from foxcast.store import archive as archive_module
from foxcast.store.archive import SourceArchive

RAW = b"<html>a filing body</html>"


@pytest.mark.unit
def test_the_default_archive_is_beside_the_data_repo_not_inside_it(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "none.toml")
    assert settings.archive_dir == default_home() / "archive"
    assert settings.data_dir not in settings.archive_dir.parents


@pytest.mark.unit
def test_the_config_file_sets_the_archive(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    write_default_config(config, data_dir=tmp_path / "data")
    assert load_settings(config).archive_dir == default_home() / "archive"
    config.write_text(f'archive_dir = "{(tmp_path / "a").as_posix()}"\n', encoding="utf-8")
    assert load_settings(config).archive_dir == tmp_path / "a"


@pytest.mark.unit
def test_the_archive_refuses_a_folder_inside_a_git_work_tree(tmp_path: Path) -> None:
    repo = tmp_path / "data"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="git"):
        SourceArchive.outside_git(repo / "sources")
    with pytest.raises(ValueError, match="git"):
        SourceArchive.outside_git(repo)


@pytest.mark.unit
def test_a_git_worktree_file_counts_as_a_work_tree(tmp_path: Path) -> None:
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    with pytest.raises(ValueError, match="git"):
        SourceArchive.outside_git(repo / "deep" / "sources")


@pytest.mark.unit
def test_a_folder_in_no_repository_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test folders sit inside this repository (pytest --basetemp), so stand in for "none".
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)
    archive = SourceArchive.outside_git(tmp_path / "archive")
    archive.save(RAW)
    assert archive.load(hashlib.sha256(RAW).hexdigest()) == RAW


def git_repo(folder: Path, ignore: str) -> Path:
    folder.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(folder)], check=True)  # noqa: S603, S607
    (folder / ".gitignore").write_text(ignore, encoding="utf-8", newline="\n")
    return folder


@pytest.mark.integration
def test_a_folder_the_repository_ignores_is_accepted(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "home", ".foxcast/archive/\n")
    SourceArchive.outside_git(repo / ".foxcast" / "archive").save(RAW)


@pytest.mark.integration
def test_a_folder_the_repository_does_not_ignore_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "home", ".foxcast/other/\n")
    with pytest.raises(ValueError, match="does not ignore"):
        SourceArchive.outside_git(repo / ".foxcast" / "archive")


@pytest.mark.integration
@pytest.mark.parametrize("rule", [".foxcast/archive/00/\n", ".foxcast/archive/*.bin\n", "*.bin\n"])
def test_an_ignore_rule_for_only_some_copies_is_refused(tmp_path: Path, rule: str) -> None:
    # Review finding 7: ignoring one shard, or the copies' names, leaves the folder open.
    repo = git_repo(tmp_path / "home", rule)
    with pytest.raises(ValueError, match="does not ignore"):
        SourceArchive.outside_git(repo / ".foxcast" / "archive")


@pytest.mark.integration
def test_inherited_git_settings_cannot_answer_for_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review finding 6: GIT_DIR pointing at a repository that ignores everything.
    lenient = git_repo(tmp_path / "lenient", "*\n")
    strict = git_repo(tmp_path / "home", ".foxcast/other/\n")
    monkeypatch.setenv("GIT_DIR", str(lenient / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(lenient))
    with pytest.raises(ValueError, match="does not ignore"):
        SourceArchive.outside_git(strict / ".foxcast" / "archive")


@pytest.mark.unit
def test_no_answer_from_git_counts_as_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*_: object, **__: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert not archive_module.ignored_by_git(tmp_path / "a", tmp_path)


@pytest.mark.unit
def test_the_settings_archive_is_usable(tmp_path: Path) -> None:
    archive = SourceArchive(Settings(archive_dir=tmp_path / "archive").archive_dir)
    archive.save(RAW)
    assert archive.load(hashlib.sha256(RAW).hexdigest()) == RAW


@pytest.mark.unit
def test_the_index_lists_hashes_and_sizes_but_no_content(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "archive")
    archive.save(RAW)
    archive.save(b"another")
    index = tmp_path / "repo" / "sources.index"
    assert archive.write_index(index) == 2
    text = index.read_bytes().decode("ascii")
    expected = sorted(f"{hashlib.sha256(raw).hexdigest()} {len(raw)}" for raw in (RAW, b"another"))
    assert text == "".join(f"{line}\n" for line in expected)
    assert b"filing" not in index.read_bytes()


@pytest.mark.unit
def test_the_index_skips_partial_writes_and_strays(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "archive")
    path = archive.save(RAW)
    (path.parent / f"{path.name}.0123.tmp").write_bytes(b"half")
    (path.parent / "notes.txt").write_bytes(b"stray")
    assert archive.write_index(tmp_path / "index") == 1


@pytest.mark.unit
def test_an_empty_archive_writes_an_empty_index(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "never-made")
    assert archive.write_index(tmp_path / "index") == 0
    assert (tmp_path / "index").read_bytes() == b""
