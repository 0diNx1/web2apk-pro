"""End-to-end run of run_build() with FAKE java/gradle/zipalign/apksigner (no Android needed).

These prove the orchestration (stages, files, signing hand-off, secrets handling). They do NOT prove that
Gradle/AGP accepts the generated project; that requires a real SDK (see README "Verified vs. untested").
"""
import os
import shutil
from pathlib import Path

import pytest

from conftest import EXAMPLE, make_exe
from web2apk.builder import BuildRequest, run_build
from web2apk.config import GlobalConfig, ProjectConfig
from web2apk.utils import BuildError, SigningError, ToolchainError, Ui

FAKE_GRADLE = r'''
if [ "$1" = "--version" ]; then echo; echo "Gradle 8.14.3"; exit 0; fi
dir=""; task=""
while [ $# -gt 0 ]; do case "$1" in -p) dir="$2"; shift;; :app:*) task="$1";; esac; shift; done
echo "> Task :app:compileKotlin"
if [ -n "$FAKE_GRADLE_FAIL" ]; then echo "Failed to find Build Tools revision 99.0.0"; exit 1; fi
case "$task" in
  *Release) d="$dir/app/build/outputs/apk/release"; mkdir -p "$d"; echo unsigned > "$d/app-release-unsigned.apk";;
  *Debug)   d="$dir/app/build/outputs/apk/debug";   mkdir -p "$d"; echo debug > "$d/app-debug.apk";;
esac
test -f "$dir/app/src/main/assets/www/index.html" || { echo "assets missing"; exit 1; }
exit 0
'''
FAKE_ZIPALIGN = 'eval last=\\${$#}; for a in "$@"; do prev2=$prev; prev=$a; done; cp "$prev2" "$prev"'
FAKE_APKSIGNER = r'''
if [ "$1" = "verify" ]; then echo "Verified using v2 scheme (APK Signature Scheme v2): true"; echo "Signer #1 certificate SHA-256 digest: abcdef0123456789abcdef"; exit 0; fi
[ -n "$W2A_CHILD_STORE_PW" ] || { echo "no password in env" >&2; exit 1; }
out=""; prev=""; for a in "$@"; do [ "$prev" = "--out" ] && out="$a"; prev="$a"; last="$a"; done
echo signed > "$out"
'''


@pytest.fixture
def toolchain(tmp_path, fake_sdk):
    jdk = tmp_path / "jdk"
    make_exe(jdk / "bin" / "java", 'echo \'openjdk version "21.0.4"\' >&2')
    make_exe(jdk / "bin" / "keytool", "true")
    gradle = make_exe(tmp_path / "gradle" / "bin" / "gradle", FAKE_GRADLE)
    for bt in ("34.0.0", "35.0.0"):
        make_exe(fake_sdk / "build-tools" / bt / "zipalign", FAKE_ZIPALIGN)
        make_exe(fake_sdk / "build-tools" / bt / "apksigner", FAKE_APKSIGNER)
    g = GlobalConfig.load()
    g.set("java_home", str(jdk))
    g.set("sdk_path", str(fake_sdk))
    g.set("gradle_path", str(gradle))
    g.save()
    return g


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "hello"
    shutil.copytree(EXAMPLE, p)
    return p


def _req(proj, g, **kw):
    cfg = ProjectConfig(**{**ProjectConfig().to_dict(), "app_name": "Hello Web", "package_id": "com.example.helloweb"})
    return BuildRequest(project_dir=proj, cfg=cfg, gcfg=g, interactive=False, **kw)


def test_debug_build_end_to_end(proj, toolchain):
    res = run_build(_req(proj, toolchain), Ui(quiet=True), env=dict(os.environ))
    assert res.apk == proj / ".web2apk" / "out" / "hello-web-1.0.0-debug.apk" and res.apk.read_text() == "debug\n"
    assert "debug key" in res.signed_with and res.size > 0 and len(res.sha256) == 64
    log = res.log_file.read_text()
    assert "assembleDebug" in log and "Task :app:compileKotlin" in log
    assert (proj / ".web2apk" / "build" / "android" / "app" / "src" / "main" / "assets" / "www" / "about.html").is_file()
    assert not (proj / ".web2apk" / "build" / "android" / "app" / "src" / "main" / "assets" / "www" / ".web2apk").exists()


def test_rebuild_does_not_package_previous_outputs(proj, toolchain):
    run_build(_req(proj, toolchain), Ui(quiet=True), env=dict(os.environ))
    run_build(_req(proj, toolchain), Ui(quiet=True), env=dict(os.environ))
    www = proj / ".web2apk" / "build" / "android" / "app" / "src" / "main" / "assets" / "www"
    assert sorted(p.name for p in www.iterdir()) == ["about.html", "index.html", "script.js", "style.css"]


def test_release_build_signs_and_never_logs_password(proj, toolchain, tmp_path):
    ks = tmp_path / "rel.p12"
    ks.write_bytes(b"k")
    ks.chmod(0o600)
    env = dict(os.environ, WEB2APK_KEYSTORE_PASSWORD="hunter2-secret", WEB2APK_KEY_ALIAS="key")
    res = run_build(_req(proj, toolchain, release=True, keystore=str(ks)), Ui(quiet=True), env=env)
    assert res.apk.name == "hello-web-1.0.0-release.apk" and res.apk.read_text() == "signed\n"
    assert "cert SHA-256 abcdef" in res.signed_with
    assert "hunter2-secret" not in res.log_file.read_text()
    for f in (proj / ".web2apk" / "build").rglob("*"):
        if f.is_file() and f.stat().st_size < 1_000_000:
            assert b"hunter2-secret" not in f.read_bytes(), f
    assert not list(res.apk.parent.glob("*.tmp"))
    with pytest.raises(BuildError):                     # refuses to overwrite a release APK
        run_build(_req(proj, toolchain, release=True, keystore=str(ks)), Ui(quiet=True), env=env)
    res2 = run_build(_req(proj, toolchain, release=True, keystore=str(ks), force=True), Ui(quiet=True), env=env)
    assert res2.apk.exists()


def test_release_without_keystore_fails_before_building(proj, toolchain):
    with pytest.raises(SigningError):
        run_build(_req(proj, toolchain, release=True), Ui(quiet=True), env=dict(os.environ))
    assert not (proj / ".web2apk" / "build").exists()      # failed fast, nothing generated


def test_gradle_failure_has_diagnostics_and_log(proj, toolchain):
    env = dict(os.environ, FAKE_GRADLE_FAIL="1")
    with pytest.raises(BuildError) as e:
        run_build(_req(proj, toolchain), Ui(quiet=True), env=env)
    assert "Build Tools" in e.value.hint or "SDK package" in e.value.hint
    assert "Full log:" in e.value.hint
    assert list((proj / ".web2apk" / "logs").glob("build-*.log"))


def test_missing_toolchain_is_reported(proj, tmp_path):
    g = GlobalConfig.load()
    g.set("sdk_path", str(tmp_path / "nosdk"))
    g.set("java_home", str(tmp_path / "nojava"))
    with pytest.raises(ToolchainError) as e:
        run_build(_req(proj, g), Ui(quiet=True), env={"PATH": str(tmp_path)})
    assert "doctor" in e.value.hint


def test_dry_run_with_missing_toolchain_still_previews(proj, tmp_path, capsys):
    g = GlobalConfig.load()
    res = run_build(_req(proj, g, dry_run=True), Ui(quiet=False), env={"PATH": str(tmp_path)})
    assert res.dry_run and not (proj / ".web2apk").exists()
