from pathlib import Path

import pytest

from invoiceops import demo_paths
from invoiceops.demo_reset import demo_resources


def test_confirmed_reset_requires_a_marked_project_local_demo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match="not initialized"):
        demo_paths.validate_demo_root(Path("var/local-demo"), require_marker=True)
    with pytest.raises(ValueError, match="canonical"):
        demo_paths.validate_demo_root(tmp_path, require_marker=True)
    with pytest.raises(ValueError, match="canonical"):
        demo_paths.validate_demo_root(Path.home(), require_marker=True)


def test_demo_root_rejects_symlink_escapes_and_invalid_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    escaped = tmp_path / "outside"
    escaped.mkdir()
    root_link = tmp_path / "var" / "local-demo"
    root_link.parent.mkdir()
    root_link.symlink_to(escaped, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        demo_paths.validate_demo_root(Path("var/local-demo"), require_marker=False)

    root_link.unlink()
    root = demo_paths.initialize_demo_root(Path("var/local-demo"))
    (root / demo_paths.DEMO_ROOT_MARKER).write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        demo_paths.validate_demo_root(root, require_marker=True)


@pytest.mark.parametrize(
    "unsafe_root",
    [Path("var/another-demo"), Path("var"), Path("."), Path.home(), Path("/tmp/invoiceops")],
)
def test_demo_root_rejects_every_noncanonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_root: Path
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match="canonical|symbolic"):
        demo_paths.validate_demo_root(unsafe_root, require_marker=False)


def test_initialize_demo_root_refuses_preexisting_non_demo_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    root = tmp_path / "var" / "local-demo"
    root.mkdir(parents=True)
    (root / "personal-notes.txt").write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError, match="contains files"):
        demo_paths.initialize_demo_root(Path("var/local-demo"))
    assert (root / "personal-notes.txt").read_text(encoding="utf-8") == "do not delete"


def test_reset_resource_preview_rejects_an_arbitrary_demo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ValueError, match="canonical"):
        demo_resources(Path("var/another-demo"))
