import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "hello-web"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Never touch the real ~/.config or ~/.cache; never inherit signing secrets."""
    monkeypatch.setenv("WEB2APK_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("WEB2APK_CACHE_DIR", str(tmp_path / "cache"))
    for k in list(os.environ):
        if k.startswith("WEB2APK_") and k not in ("WEB2APK_CONFIG_DIR", "WEB2APK_CACHE_DIR"):
            monkeypatch.delenv(k)


@pytest.fixture
def web(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    (d / "index.html").write_text('<!doctype html><meta name="viewport" content="width=device-width"><h1>x</h1>')
    (d / "css").mkdir()
    (d / "css" / "a.css").write_text("body{}")
    (d / "js").mkdir()
    (d / "js" / "a.js").write_text("1")
    return d


@pytest.fixture
def fake_sdk(tmp_path):
    root = tmp_path / "sdk"
    for lvl in (33, 34, 35):
        (root / "platforms" / f"android-{lvl}").mkdir(parents=True)
    (root / "platforms" / "android-35-ext").mkdir()
    for bt in ("34.0.0", "35.0.0"):
        d = root / "build-tools" / bt
        d.mkdir(parents=True)
        for tool in ("apksigner", "zipalign"):
            (d / tool).write_text("#!/bin/sh\n")
    (root / "platform-tools").mkdir()
    (root / "platform-tools" / "adb").write_text("#!/bin/sh\n")
    (root / "licenses").mkdir()
    (root / "licenses" / "android-sdk-license").write_text("x")
    return root


def make_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
