import json
import shutil
from pathlib import Path

import pytest

from conftest import EXAMPLE
from web2apk import cli
from web2apk.utils import ExitCode


def run(argv, capsys):
    code = cli.main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_help_and_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    capsys.readouterr()
    code, out, _ = run(["version"], capsys)
    assert code == 0 and out.startswith("web2apk ")
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0


def test_no_command_and_bad_args_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        cli.main(["build", "x", "--min-sdk", "abc"])
    assert e.value.code == 2


def test_parser_parses_build_flags():
    a = cli.build_parser().parse_args(["build", "./app", "--name", "My App", "--package", "com.a.b", "--icon", "i.png",
                                       "--min-sdk", "23", "--target-sdk", "35", "--release", "--no-fullscreen",
                                       "--internet", "-q", "--no-interaction", "--dry-run"])
    assert (a.app_name, a.package_id, a.icon, a.min_sdk, a.target_sdk) == ("My App", "com.a.b", "i.png", 23, 35)
    assert a.release and a.fullscreen is False and a.internet_access is True and a.dry_run
    assert a.quiet and a.no_interaction
    b = cli.build_parser().parse_args(["build", "p"])
    assert b.fullscreen is None and not hasattr(b, "quiet")


def test_build_missing_directory_exit_code(capsys, tmp_path):
    code, _, err = run(["build", str(tmp_path / "nope"), "--no-interaction"], capsys)
    assert code == ExitCode.CONFIG and "not found" in err


def test_build_invalid_config_values(capsys):
    code, _, err = run(["build", str(EXAMPLE), "--package", "Bad Package", "--no-interaction", "--dry-run"], capsys)
    assert code == ExitCode.CONFIG and "package_id" in err


def test_build_no_entry_point(capsys, tmp_path):
    (tmp_path / "readme.txt").write_text("x")
    code, _, err = run(["build", str(tmp_path), "--no-interaction", "--dry-run"], capsys)
    assert code == ExitCode.CONFIG and "No index.html" in err


def test_dry_run_changes_nothing(capsys, tmp_path):
    proj = tmp_path / "hello"
    shutil.copytree(EXAMPLE, proj)
    before = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*"))
    code, out, err = run(["build", str(proj), "--dry-run", "--no-interaction"], capsys)
    assert code == 0 and "DRY RUN" in err and out == ""
    assert sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*")) == before


def test_release_dry_run_mentions_signing(capsys, tmp_path):
    proj = tmp_path / "hello"
    shutil.copytree(EXAMPLE, proj)
    code, _, err = run(["build", str(proj), "--release", "--dry-run", "--no-interaction"], capsys)
    assert code == 0 and "apksigner" in err


def test_release_refuses_existing_apk_without_force(capsys, tmp_path):
    proj = tmp_path / "hello"
    shutil.copytree(EXAMPLE, proj)
    out = tmp_path / "out"
    out.mkdir()
    (out / "hello-web-1.0.0-release.apk").write_bytes(b"precious")
    code, _, err = run(["build", str(proj), "--release", "--dry-run", "--no-interaction", "--output-dir", str(out)], capsys)
    assert code == ExitCode.BUILD and "Refusing to overwrite" in err
    assert (out / "hello-web-1.0.0-release.apk").read_bytes() == b"precious"


def test_init_creates_project_and_refuses_overwrite(capsys, tmp_path):
    target = tmp_path / "my-web-app"
    code, _, _ = run(["init", str(target), "--no-interaction"], capsys)
    assert code == 0
    assert {p.name for p in target.iterdir()} == {"index.html", "style.css", "script.js", "web2apk.json"}
    cfg = json.loads((target / "web2apk.json").read_text())
    assert cfg["package_id"] == "com.example.mywebapp" and cfg["app_name"] == "My Web App"
    code, _, err = run(["init", str(target)], capsys)
    assert code == ExitCode.CONFIG and "refusing" in err
    code, _, err = run(["init", str(tmp_path / "x"), "--package", "bad"], capsys)
    assert code == ExitCode.CONFIG


def test_init_escapes_html_in_name(capsys, tmp_path):
    t = tmp_path / "a"
    run(["init", str(t), "--name", "<b>x</b>"], capsys)
    assert "<b>x</b>" not in (t / "index.html").read_text()


def test_build_derives_defaults_when_not_interactive(capsys, tmp_path):
    proj = tmp_path / "plain-site"
    proj.mkdir()
    (proj / "index.html").write_text("<meta name=viewport content=x>")
    code, _, err = run(["build", str(proj), "--dry-run", "--no-interaction"], capsys)
    assert code == 0 and "com.example.plainsite" in err
    assert not (proj / "web2apk.json").exists()       # nothing saved without consent


def test_config_commands(capsys):
    assert run(["config", "set", "sdk_path", "/opt/android-sdk"], capsys)[0] == 0
    code, out, _ = run(["config", "get", "sdk_path"], capsys)
    assert code == 0 and out.strip() == "/opt/android-sdk"
    assert run(["config", "set", "defaults.orientation", "portrait"], capsys)[0] == 0
    assert run(["config", "set", "bogus", "1"], capsys)[0] == ExitCode.CONFIG
    assert run(["config", "unset", "sdk_path"], capsys)[0] == 0
    assert run(["config", "get", "sdk_path"], capsys)[0] == ExitCode.CONFIG
    code, out, _ = run(["config", "path"], capsys)
    assert code == 0 and out.strip().endswith("config.json")


def test_doctor_reports_with_fake_environment(capsys, fake_sdk, monkeypatch):
    run(["config", "set", "sdk_path", str(fake_sdk)], capsys)
    code, _, err = run(["doctor", "--no-color"], capsys)
    assert "Android SDK" in err and "Build-tools" in err and "Platform android-35" in err
    assert code in (ExitCode.OK, ExitCode.ENVIRONMENT)   # depends on whether this box has a JDK


def test_clean_only_removes_own_build_dir(capsys, tmp_path):
    proj = tmp_path / "p"
    (proj / ".web2apk" / "build" / "android").mkdir(parents=True)
    (proj / ".web2apk" / "build" / "android" / ".web2apk-build").write_text("x")
    (proj / ".web2apk" / "out").mkdir()
    (proj / ".web2apk" / "out" / "a.apk").write_text("x")
    (proj / "index.html").write_text("x")
    assert run(["clean", str(proj), "--dry-run"], capsys)[0] == 0
    assert (proj / ".web2apk" / "build").exists()
    assert run(["clean", str(proj)], capsys)[0] == 0
    assert not (proj / ".web2apk" / "build").exists()
    assert (proj / ".web2apk" / "out" / "a.apk").exists() and (proj / "index.html").exists()
    # marker missing -> refuse
    (proj / ".web2apk" / "build" / "x").mkdir(parents=True)
    assert run(["clean", str(proj)], capsys)[0] == ExitCode.CONFIG
    assert (proj / ".web2apk" / "build" / "x").exists()


def test_clean_refuses_symlinked_root(capsys, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    victim = tmp_path / "victim"
    (victim / "build" / "android").mkdir(parents=True)
    (victim / "build" / "android" / ".web2apk-build").write_text("x")
    (proj / ".web2apk").symlink_to(victim)
    code, _, _ = run(["clean", str(proj)], capsys)
    assert code == ExitCode.CONFIG and (victim / "build").exists()


def test_verify_missing_file(capsys, tmp_path):
    assert run(["verify", str(tmp_path / "x.apk")], capsys)[0] == ExitCode.CONFIG


def test_keystore_create_noninteractive_needs_env_password(capsys, tmp_path):
    code, _, _ = run(["keystore", "create", str(tmp_path / "k.p12"), "--alias", "a", "--no-interaction"], capsys)
    assert code in (ExitCode.ABORTED, ExitCode.ENVIRONMENT)     # ENVIRONMENT when no JDK on this machine
