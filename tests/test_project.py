import os

import pytest

from web2apk.config import ProjectConfig
from web2apk.project import collect_files, detect_web_root_and_entry, inspect_project, sync_assets
from web2apk.utils import ConfigError


def test_entry_autodetect_root(web):
    root, entry = detect_web_root_and_entry(web, ProjectConfig())
    assert root == web.resolve() and entry == "index.html"




def test_entry_in_public_dir(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("x")
    root, entry = detect_web_root_and_entry(tmp_path, ProjectConfig())
    assert root == (tmp_path / "public").resolve() and entry == "index.html"


def test_entry_in_assets_dir_and_root_assets_folder_ignored(tmp_path):
    (tmp_path / "index.html").write_text("x")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index.html").write_text("other")
    root, _ = detect_web_root_and_entry(tmp_path, ProjectConfig())
    assert root == tmp_path.resolve()


def test_custom_entry_and_web_root(web):
    (web / "pages").mkdir()
    (web / "pages" / "home.html").write_text("x")
    root, entry = detect_web_root_and_entry(web, ProjectConfig(entry_point="pages/home.html"))
    assert entry == "pages/home.html"
    root, entry = detect_web_root_and_entry(web, ProjectConfig(web_root="pages", entry_point="home.html"))
    assert root == (web / "pages").resolve()


def test_missing_entry_gives_helpful_error(tmp_path):
    (tmp_path / "other.html").write_text("x")
    with pytest.raises(ConfigError) as e:
        detect_web_root_and_entry(tmp_path, ProjectConfig())
    assert "No index.html" in str(e.value) and "other.html" in (e.value.hint or "")
    with pytest.raises(ConfigError) as e:
        detect_web_root_and_entry(tmp_path, ProjectConfig(entry_point="nope.html"))
    assert "not found" in str(e.value)


def test_traversal_entry_rejected(web):
    with pytest.raises(ConfigError):
        detect_web_root_and_entry(web, ProjectConfig(entry_point="../x.html"))
    with pytest.raises(ConfigError):
        detect_web_root_and_entry(web, ProjectConfig(web_root="../"))


def test_invalid_project_dirs(tmp_path):
    with pytest.raises(ConfigError):
        inspect_project(tmp_path / "missing", ProjectConfig())
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(ConfigError):
        inspect_project(f, ProjectConfig())


def test_collect_excludes_hidden_secrets_symlinks(web, tmp_path):
    (web / ".env").write_text("SECRET=1")
    (web / ".git").mkdir()
    (web / ".git" / "config").write_text("x")
    (web / "node_modules").mkdir()
    (web / "node_modules" / "m.js").write_text("x")
    (web / "release.keystore").write_text("k")
    (web / "web2apk.json").write_text("{}")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (web / "leak.txt").symlink_to(outside)
    (web / "linkdir").symlink_to(tmp_path)
    out = web / "dist"
    out.mkdir()
    (out / "old.apk").write_text("x")
    files, skipped, warnings = collect_files(web, exclude_paths=[out])
    rels = {f.rel for f in files}
    assert rels == {"index.html", "css/a.css", "js/a.js"}
    assert any("keystore" in w for w in warnings)
    assert any("leak.txt" in s and "symlink" in s for s in skipped)


def test_sync_assets_copies_updates_and_prunes(web, tmp_path):
    proj = inspect_project(web, ProjectConfig())
    dest = tmp_path / "dest" / "www"
    assert sync_assets(proj.files, dest) == (3, 0, 0)
    assert (dest / "css" / "a.css").read_text() == "body{}"
    assert sync_assets(proj.files, dest) == (0, 3, 0)
    (web / "js" / "a.js").write_text("2")
    (dest / "stale.txt").write_text("x")
    (dest / "gone").mkdir()
    (dest / "gone" / "f").write_text("x")
    proj = inspect_project(web, ProjectConfig())
    copied, unchanged, removed = sync_assets(proj.files, dest)
    assert (copied, unchanged, removed) == (1, 2, 2)
    assert not (dest / "gone").exists()


def test_html_warnings(web):
    (web / "index.html").write_text('<img src="http://x/y.png"><script src="https://cdn/x.js"></script>')
    proj = inspect_project(web, ProjectConfig())
    text = " ".join(proj.warnings)
    assert "viewport" in text and "http://" in text and "internet_access is false" in text
