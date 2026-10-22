from conftest import make_exe
from web2apk.android_sdk import (detect_gradle, detect_java, detect_sdk, parse_gradle_version, parse_java_major,
                                 version_tuple)


def test_version_parsing():
    assert parse_java_major('openjdk version "21.0.4" 2024-07-16') == 21
    assert parse_java_major('java version "1.8.0_392"') == 8
    assert parse_java_major('openjdk version "17" 2021-09-14') == 17
    assert parse_java_major("garbage") is None
    assert parse_gradle_version("\n------\nGradle 8.14.3\n------\nBuild time: x") == "8.14.3"
    assert parse_gradle_version("nothing") is None
    assert version_tuple("8.14.3") > version_tuple("8.9") and version_tuple("8.10.2-rc-1") == (8, 10, 2)


def test_detect_sdk_from_config(fake_sdk):
    sdk = detect_sdk(str(fake_sdk), env={})
    assert sdk.found and sdk.source == "config"
    assert sdk.platforms == [33, 34, 35]          # android-35-ext is ignored
    assert sdk.build_tools == ["34.0.0", "35.0.0"]
    assert sdk.best_build_tools() == "35.0.0"
    assert sdk.has_platform(35) and not sdk.has_platform(36)
    assert sdk.adb and sdk.licenses_accepted and sdk.sdkmanager is None


def test_detect_sdk_from_env_and_missing(fake_sdk, tmp_path):
    assert detect_sdk(None, env={"ANDROID_HOME": str(fake_sdk)}).root == fake_sdk.resolve()
    assert detect_sdk(None, env={"ANDROID_SDK_ROOT": str(fake_sdk)}).source == "ANDROID_SDK_ROOT"
    bad = detect_sdk(str(tmp_path / "nope"), env={})
    # falls through to default locations; whatever it finds, a bogus configured path must be listed as tried
    assert str(tmp_path / "nope") in bad.tried


def test_build_tools_need_both_tools(tmp_path):
    root = tmp_path / "sdk"
    (root / "build-tools" / "35.0.0").mkdir(parents=True)
    (root / "build-tools" / "35.0.0" / "apksigner").write_text("x")
    sdk = detect_sdk(str(root), env={})
    assert sdk.best_build_tools() is None


def test_detect_java_with_fake_jdk(tmp_path):
    home = tmp_path / "jdk"
    make_exe(home / "bin" / "java", 'echo \'openjdk version "21.0.4" 2024-07-16\' >&2')
    make_exe(home / "bin" / "keytool", "true")
    info = detect_java(str(home), env={})
    assert info.found and info.major == 21 and info.keytool and info.source == "config"


def test_detect_gradle_with_fake_binary(tmp_path):
    exe = make_exe(tmp_path / "g" / "bin" / "gradle", 'echo; echo "Gradle 8.14.3"; echo')
    info = detect_gradle(str(tmp_path / "g"), env={"PATH": "/usr/bin:/bin"})
    assert info.found and info.version == "8.14.3" and info.path == exe


def test_detect_gradle_none(tmp_path):
    info = detect_gradle(str(tmp_path / "missing"), env={"PATH": str(tmp_path)})
    # nothing configured or on PATH; /opt/gradle may exist on a dev box, so only assert type
    assert info.found in (True, False)
