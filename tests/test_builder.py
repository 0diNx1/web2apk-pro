import re
from pathlib import Path

import pytest

from conftest import EXAMPLE
from web2apk import builder
from web2apk.android_sdk import GradleInfo, detect_sdk
from web2apk.config import GlobalConfig, ProjectConfig
from web2apk.project import inspect_project
from web2apk.utils import ToolchainError, Ui


def test_gradle_command_generation(tmp_path):
    d = builder.gradle_command(Path("/opt/gradle/bin/gradle"), tmp_path, release=False)
    assert d[0] == "/opt/gradle/bin/gradle" and d[-1] == ":app:assembleDebug"
    assert "-p" in d and "--console=plain" in d and "--offline" not in d
    r = builder.gradle_command(Path("gradlew"), tmp_path, release=True, offline=True, stacktrace=True)
    assert r[-1] == ":app:assembleRelease" and "--offline" in r and "--stacktrace" in r


def test_gradle_compat_and_choice():
    assert builder.gradle_supported("8.7.3", "8.14.3")[0] is True
    assert builder.gradle_supported("8.7.3", "8.5")[0] is False
    assert builder.gradle_supported("8.7.3", "9.0.0")[0] is False
    assert builder.gradle_supported("9.9.9", "8.14.3")[0] is None
    new = GradleInfo(Path("/x/gradle"), "8.14.3", "PATH")
    old = GradleInfo(Path("/x/gradle"), "4.4.1", "PATH")
    none = GradleInfo(None)
    assert builder.choose_gradle(new, "8.7.3", "auto")[0] == "system"
    assert builder.choose_gradle(old, "8.7.3", "auto")[0] == "wrapper"
    assert builder.choose_gradle(none, "8.7.3", "auto")[0] == "wrapper"
    assert builder.choose_gradle(new, "8.7.3", "wrapper")[0] == "wrapper"
    with pytest.raises(ToolchainError):
        builder.choose_gradle(none, "8.7.3", "system")


def test_failure_diagnosis():
    assert any("Build Tools" in h or "SDK package" in h for h in builder.diagnose_failure("Failed to find Build Tools revision 34.0.0"))
    assert any("download" in h for h in builder.diagnose_failure("Could not resolve com.android.tools.build:gradle"))
    assert any("Java" in h for h in builder.diagnose_failure("Unsupported class file major version 65"))
    assert any("Android SDK" in h for h in builder.diagnose_failure("SDK location not found. Define a valid SDK"))
    assert builder.diagnose_failure("all fine") == []


def test_render_rejects_unknown_placeholder():
    assert builder.render("a @@X@@ b", {"X": "1"}) == "a 1 b"
    with pytest.raises(Exception):
        builder.render("@@NOPE@@", {})


def _generate(tmp_path, fake_sdk, cfg=None, project=EXAMPLE):
    cfg = cfg or ProjectConfig(**{**ProjectConfig().to_dict(), "app_name": "Hello Web", "package_id": "com.example.helloweb",
                                  "js_bridge": True})
    gcfg = GlobalConfig.load()
    web = inspect_project(project, cfg)
    sdk = detect_sdk(str(fake_sdk), env={})
    out = tmp_path / "build"
    stats = builder.generate_project(cfg, web, sdk, "35.0.0", gcfg, out, Ui(quiet=True))
    return out, stats, cfg


def test_generate_project_layout_and_no_placeholders(tmp_path, fake_sdk):
    out, stats, cfg = _generate(tmp_path, fake_sdk)
    for rel in ("settings.gradle.kts", "build.gradle.kts", "gradle.properties", "local.properties",
                "app/build.gradle.kts", "app/src/main/AndroidManifest.xml",
                "app/src/main/java/com/example/helloweb/MainActivity.kt",
                "app/src/main/java/com/example/helloweb/AppConfig.kt",
                "app/src/main/res/values/strings.xml", "app/src/main/res/values/themes.xml",
                "app/src/main/res/mipmap-xxxhdpi/ic_launcher.png",
                "app/src/main/assets/www/index.html", "app/src/main/assets/www/about.html",
                "app/src/main/assets/www/style.css", "app/src/main/assets/web2apk/error.html", ".web2apk-build"):
        assert (out / rel).is_file(), rel
    for p in out.rglob("*"):
        if p.is_file() and p.suffix in {".kt", ".kts", ".xml", ".properties", ".html"}:
            assert "@@" not in p.read_text(), p
    assert not (out / "app/src/main/assets/www/web2apk.json").exists()   # config is not shipped
    app_cfg = (out / "app/src/main/java/com/example/helloweb/AppConfig.kt").read_text()
    assert 'START_URL = "https://appassets.androidplatform.net/assets/www/index.html"' in app_cfg
    assert "const val INTERNET = false" in app_cfg and "const val JS_BRIDGE = true" in app_cfg
    assert "emptyList()" in app_cfg
    assert "sdk.dir=" in (out / "local.properties").read_text()
    gradle = (out / "app/build.gradle.kts").read_text()
    assert 'applicationId = "com.example.helloweb"' in gradle and "compileSdk = 35" in gradle
    assert "storePassword" not in gradle and "signingConfig" not in gradle


def test_manifest_permissions_are_opt_in(tmp_path, fake_sdk):
    out, _, _ = _generate(tmp_path, fake_sdk)
    manifest = (out / "app/src/main/AndroidManifest.xml").read_text()
    assert "uses-permission" not in manifest                       # offline app: no permissions at all
    assert 'usesCleartextTraffic="false"' in manifest and 'allowBackup="false"' in manifest

    cfg = ProjectConfig(app_name="Net", package_id="com.example.net", internet_access=True,
                        permissions=["camera", "location"], allowed_origins=["example.com", "*.example.org"],
                        allow_downloads=True, orientation="landscape")
    out2, _, _ = _generate(tmp_path / "two", fake_sdk, cfg)
    m = (out2 / "app/src/main/AndroidManifest.xml").read_text()
    for perm in ("INTERNET", "ACCESS_NETWORK_STATE", "CAMERA", "ACCESS_FINE_LOCATION"):
        assert f"android.permission.{perm}" in m
    assert "RECORD_AUDIO" not in m and 'screenOrientation="landscape"' in m
    k = (out2 / "app/src/main/java/com/example/net/AppConfig.kt").read_text()
    assert 'listOf("example.com", "*.example.org")' in k and "ALLOW_DOWNLOADS = true" in k


def test_special_characters_are_escaped(tmp_path, fake_sdk):
    cfg = ProjectConfig(app_name='Tom\'s "App" $x & <y>', package_id="com.example.tom")
    out, _, _ = _generate(tmp_path, fake_sdk, cfg)
    assert "Tom\\'s \\\"App\\\" $x &amp; &lt;y&gt;" in (out / "app/src/main/res/values/strings.xml").read_text()
    assert 'APP_NAME = "Tom\'s \\"App\\" \\$x & <y>"' in (out / "app/src/main/java/com/example/tom/AppConfig.kt").read_text()


def test_regeneration_is_incremental_and_prunes_old_package(tmp_path, fake_sdk):
    out, first, cfg = _generate(tmp_path, fake_sdk)
    _, second, _ = builder.generate_project, None, None
    gcfg = GlobalConfig.load()
    web = inspect_project(EXAMPLE, cfg)
    sdk = detect_sdk(str(fake_sdk), env={})
    stats = builder.generate_project(cfg, web, sdk, "35.0.0", gcfg, out, Ui(quiet=True))
    assert stats["changed"] == 0 and stats["assets_copied"] == 0          # nothing rewritten
    cfg2 = ProjectConfig(**{**cfg.to_dict(), "package_id": "com.example.other"})
    builder.generate_project(cfg2, web, sdk, "35.0.0", gcfg, out, Ui(quiet=True))
    assert not (out / "app/src/main/java/com/example/helloweb").exists()
    assert (out / "app/src/main/java/com/example/other/MainActivity.kt").is_file()


def test_refuses_foreign_build_dir(tmp_path):
    d = tmp_path / "notmine"
    d.mkdir()
    (d / "precious.txt").write_text("x")
    with pytest.raises(Exception):
        builder.ensure_build_dir(d)


def test_default_icon_generated_without_pillow(tmp_path, fake_sdk):
    out, _, _ = _generate(tmp_path, fake_sdk)
    png = (out / "app/src/main/res/mipmap-mdpi/ic_launcher.png").read_bytes()
    assert png.startswith(b"\x89PNG")


def test_missing_icon_file_is_an_error(tmp_path, fake_sdk):
    cfg = ProjectConfig(app_name="X", package_id="com.example.x", icon="nope.png")
    with pytest.raises(Exception):
        _generate(tmp_path, fake_sdk, cfg)


def test_output_name():
    assert builder.output_name(ProjectConfig(app_name="My App", version_name="2.1"), True) == "my-app-2.1-release.apk"
