"""Command-line interface for Web2APK Pro."""
from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .android_sdk import detect_gradle, detect_java, detect_sdk
from .builder import (BUILD_MARKER, TEMPLATE_ROOT, BuildRequest, print_summary, run_build, clean_env)
from .config import (PROJECT_CONFIG_NAME, GlobalConfig, ProjectConfig, resolve_project_config,
                     save_project_file)
from .diagnostics import render_checks, run_checks
from .signing import (ENV_NEW_STORE_PASS, apksigner_verify_command, create_keystore)
from .utils import (AbortedError, ConfigError, ExitCode, SigningError, ToolchainError, Ui, Web2ApkError,
                    is_within, slugify)
from . import validation as v

STARTER_DIR = Path(__file__).parent / "templates" / "starter"


# --------------------------------------------------------------------------- parser

def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("global options")
    g.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                   help="only print warnings, errors and the final APK path")
    g.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                   help="show Gradle output and debug details")
    g.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="disable coloured output")
    g.add_argument("--no-interaction", action="store_true", default=argparse.SUPPRESS,
                   help="never prompt; fail instead (for CI and scripts)")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="web2apk", parents=[common],
        description="Package a plain HTML/CSS/JavaScript project as an installable Android APK (WebView).",
        epilog="Exit codes: 0 ok, 1 error, 2 usage, 3 config/project, 4 toolchain, 5 build, 6 signing, 7 needs interaction.")
    parser.add_argument("--version", action="version", version=f"web2apk {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    B = argparse.BooleanOptionalAction

    p = sub.add_parser("init", parents=[common], help="create a starter web project")
    p.add_argument("directory")
    p.add_argument("--name", help="display name of the app")
    p.add_argument("--package", help="Android package id, e.g. com.example.app")

    p = sub.add_parser("build", parents=[common], help="build an APK from a web project")
    p.add_argument("project", nargs="?", default=".", help="project directory (default: .)")
    p.add_argument("--release", action="store_true", help="build a release APK signed with your keystore")
    p.add_argument("--name", dest="app_name")
    p.add_argument("--package", dest="package_id")
    p.add_argument("--icon")
    p.add_argument("--min-sdk", type=int, dest="min_sdk")
    p.add_argument("--target-sdk", type=int, dest="target_sdk")
    p.add_argument("--version-name", dest="version_name")
    p.add_argument("--version-code", type=int, dest="version_code")
    p.add_argument("--entry", dest="entry_point", help="HTML entry point relative to the web root")
    p.add_argument("--web-root", dest="web_root", help="sub-directory containing the web files (e.g. public)")
    p.add_argument("--orientation", choices=v.ORIENTATIONS)
    p.add_argument("--status-bar-color", dest="status_bar_color")
    p.add_argument("--splash-color", dest="splash_color")
    p.add_argument("--fullscreen", action=B, default=None)
    p.add_argument("--internet", dest="internet_access", action=B, default=None, help="declare INTERNET permission")
    p.add_argument("--uploads", dest="allow_file_uploads", action=B, default=None)
    p.add_argument("--downloads", dest="allow_downloads", action=B, default=None)
    p.add_argument("--js-bridge", dest="js_bridge", action=B, default=None)
    p.add_argument("--debug-webview", dest="debug_webview", action=B, default=None)
    p.add_argument("--output-dir", help="where to put the APK (default: <project>/.web2apk/out)")
    p.add_argument("--keystore", help="release keystore (or env WEB2APK_KEYSTORE)")
    p.add_argument("--key-alias", help="key alias (or env WEB2APK_KEY_ALIAS)")
    p.add_argument("--force", action="store_true", help="allow overwriting an existing release APK")
    p.add_argument("--offline", action="store_true", help="pass --offline to Gradle (needs a warm cache)")
    p.add_argument("--gradle-mode", choices=("auto", "system", "wrapper"))
    p.add_argument("--dry-run", action="store_true", help="validate and show what would happen; change nothing")

    p = sub.add_parser("doctor", parents=[common], help="check Java, SDK, Gradle and signing setup")

    p = sub.add_parser("config", parents=[common], help="show or edit global defaults")
    csub = p.add_subparsers(dest="config_cmd", metavar="ACTION")
    csub.add_parser("show", parents=[common], help="show configuration and detected tools")
    csub.add_parser("path", parents=[common], help="print the config file path")
    c = csub.add_parser("get", parents=[common]); c.add_argument("key")
    c = csub.add_parser("set", parents=[common]); c.add_argument("key"); c.add_argument("value")
    c = csub.add_parser("unset", parents=[common]); c.add_argument("key")

    p = sub.add_parser("keystore", parents=[common], help="manage release keystores")
    ksub = p.add_subparsers(dest="ks_cmd", metavar="ACTION")
    ksub.required = True
    c = ksub.add_parser("create", parents=[common], help="create a new PKCS12 keystore (never overwrites)")
    c.add_argument("path")
    c.add_argument("--alias", required=True)
    c.add_argument("--dname", help='certificate subject, e.g. "CN=Jane Doe, O=Acme, C=US"')
    c.add_argument("--validity-days", type=int, default=10000)

    p = sub.add_parser("verify", parents=[common], help="verify an APK signature with apksigner")
    p.add_argument("apk")

    p = sub.add_parser("clean", parents=[common], help="remove generated build files of a project")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--logs", action="store_true", help="also delete build logs")
    p.add_argument("--all", action="store_true", help="also delete built APKs in .web2apk/out")
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("version", parents=[common], help="print the version")
    return parser


# --------------------------------------------------------------------------- helpers

def _ask(ui: Ui, question: str, default: str, validator=None) -> str:
    while True:
        sys.stderr.write(f"{question} [{default}]: ")
        sys.stderr.flush()
        line = sys.stdin.readline()
        if line == "":
            raise AbortedError("No input available.", hint="Pass the value on the command line or use a web2apk.json.")
        ans = line.strip() or default
        err = validator(ans) if validator else None
        if not err:
            return ans
        ui.error(err)


def _confirm(question: str, default: bool = True) -> bool:
    sys.stderr.write(f"{question} [{'Y/n' if default else 'y/N'}]: ")
    sys.stderr.flush()
    ans = sys.stdin.readline().strip().lower()
    return default if not ans else ans.startswith("y")


def derive_defaults(project_dir: Path) -> Dict[str, str]:
    base = project_dir.resolve().name
    name = re.sub(r"[-_]+", " ", base).strip().title() or "Web App"
    seg = re.sub(r"[^a-z0-9]", "", base.lower()) or "app"
    if not seg[0].isalpha():
        seg = "app" + seg
    if seg in v.RESERVED_SEGMENTS:
        seg += "app"
    return {"app_name": name, "package_id": f"com.example.{seg}"}


def _project_dir(arg: str) -> Path:
    p = Path(arg).expanduser()
    if not p.exists():
        raise ConfigError(f"Project directory not found: {p}",
                          hint="Create a starter project with: web2apk init " + str(p))
    if not p.is_dir():
        raise ConfigError(f"Not a directory: {p}")
    return p.resolve()


# --------------------------------------------------------------------------- commands

def cmd_init(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    target = Path(args.directory).expanduser()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ConfigError(f"{target} already exists and is not empty; refusing to overwrite.")
    defaults = derive_defaults(target)
    name = args.name or defaults["app_name"]
    pkg = args.package or defaults["package_id"]
    errs = [e for e in (v.check_app_name(name), v.check_package_id(pkg)) if e]
    if errs:
        raise ConfigError("; ".join(errs))
    target.mkdir(parents=True, exist_ok=True)
    for src in sorted(STARTER_DIR.iterdir()):
        text = src.read_text(encoding="utf-8").replace("@@APP_NAME_HTML@@", html.escape(name))
        (target / src.name).write_text(text, encoding="utf-8")
    cfg = {"app_name": name, "package_id": pkg, "version_name": "1.0.0", "version_code": 1,
           "entry_point": "index.html", "min_sdk": 23, "target_sdk": 35, "orientation": "auto",
           "internet_access": False, "fullscreen": False, "allow_file_uploads": True,
           "allow_downloads": False, "debug_webview": False}
    (target / PROJECT_CONFIG_NAME).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    ui.ok(f"Created {target}/ (index.html, style.css, script.js, {PROJECT_CONFIG_NAME})")
    ui.info(f"Next: web2apk build {target}")
    return ExitCode.OK


def cmd_build(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    project_dir = _project_dir(args.project)
    keys = ("app_name", "package_id", "icon", "min_sdk", "target_sdk", "version_name", "version_code",
            "entry_point", "web_root", "orientation", "status_bar_color", "splash_color", "fullscreen",
            "internet_access", "allow_file_uploads", "allow_downloads", "js_bridge", "debug_webview")
    overrides: Dict[str, Any] = {k: getattr(args, k, None) for k in keys}
    cfg, explicit = resolve_project_config(project_dir, gcfg, overrides)

    missing = [k for k in ("app_name", "package_id") if k not in explicit]
    if missing:
        d = derive_defaults(project_dir)
        if interactive and not args.dry_run:
            ui.info("Essential app settings are missing; press Enter to accept the suggested value.")
            if "app_name" in missing:
                cfg.app_name = _ask(ui, "App name", d["app_name"], v.check_app_name)
            if "package_id" in missing:
                cfg.package_id = _ask(ui, "Package ID", d["package_id"], v.check_package_id)
            if _confirm(f"Save these to {PROJECT_CONFIG_NAME}?"):
                save_project_file(project_dir, {k: getattr(cfg, k) for k in missing})
        else:
            for k in missing:
                setattr(cfg, k, d[k])
            ui.warn(f"{', '.join(missing)} not configured; using {', '.join(f'{k}={d[k]!r}' for k in missing)}")
    cfg.validate(gcfg.compile_sdk)

    req = BuildRequest(project_dir=project_dir, cfg=cfg, gcfg=gcfg, release=args.release,
                       output_dir=Path(args.output_dir) if args.output_dir else
                       (Path(gcfg.get("output_dir")) if gcfg.get("output_dir") else None),
                       dry_run=args.dry_run, offline=args.offline, force=args.force, interactive=interactive,
                       gradle_mode=args.gradle_mode, keystore=args.keystore, key_alias=args.key_alias)
    res = run_build(req, ui)
    print_summary(ui, res)
    if not res.dry_run:
        print(res.apk)  # stdout: only the APK path, handy for scripts
    return ExitCode.OK


def cmd_doctor(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    ui.info(f"Web2APK Pro {__version__} - environment check\n")
    return render_checks(ui, run_checks(gcfg))


def cmd_config(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    action = getattr(args, "config_cmd", None) or "show"
    if action == "path":
        print(gcfg.path)
    elif action == "get":
        val = gcfg.get(args.key)
        if val is None:
            raise ConfigError(f"'{args.key}' is not set")
        print(json.dumps(val) if not isinstance(val, str) else val)
    elif action == "set":
        gcfg.set(args.key, args.value)
        gcfg.save()
        ui.ok(f"{args.key} saved to {gcfg.path}")
    elif action == "unset":
        if not gcfg.unset(args.key):
            raise ConfigError(f"'{args.key}' is not set")
        gcfg.save()
        ui.ok(f"{args.key} removed")
    else:
        ui.info(f"Config file: {gcfg.path}" + ("" if gcfg.path.exists() else " (not created yet)"))
        ui.info(json.dumps(gcfg.data, indent=2) if gcfg.data else "  (no overrides; built-in defaults in effect)")
        java = detect_java(gcfg.get("java_home"))
        sdk = detect_sdk(gcfg.get("sdk_path"))
        gr = detect_gradle(gcfg.get("gradle_path"), None, java.home)
        ui.info("\nEffective toolchain:")
        ui.info(f"  java    {java.home} (JDK {java.major})" if java.found else "  java    not found")
        ui.info(f"  sdk     {sdk.root}" if sdk.found else "  sdk     not found")
        ui.info(f"  gradle  {gr.path} ({gr.version})" if gr.found else "  gradle  not found")
        ui.info(f"  pins    AGP {gcfg.agp_version}, Kotlin {gcfg.kotlin_version}, Gradle(wrapper) {gcfg.gradle_version}, compileSdk {gcfg.compile_sdk}")
    return ExitCode.OK


def cmd_keystore(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    java = detect_java(gcfg.get("java_home"))
    if not java.keytool:
        raise ToolchainError("keytool not found.", hint="sudo apt install openjdk-21-jdk")
    path = Path(args.path).expanduser()
    if path.exists():
        raise SigningError(f"Refusing to overwrite existing keystore: {path}")
    pw = os.environ.get(ENV_NEW_STORE_PASS, "")
    if not pw:
        if not interactive:
            raise AbortedError(f"Set {ENV_NEW_STORE_PASS} to create a keystore non-interactively.")
        pw = getpass.getpass("New keystore password (min 6 chars): ", stream=sys.stderr)
        if pw != getpass.getpass("Repeat password: ", stream=sys.stderr):
            raise SigningError("Passwords do not match.")
    dname = args.dname or (f"CN={_ask(ui, 'Your name or organisation', args.alias)}" if interactive else f"CN={args.alias}")
    create_keystore(java.keytool, path, args.alias, dname, pw, clean_env(java, detect_sdk(gcfg.get("sdk_path"))))
    ui.ok(f"Created {path} (permissions 600, alias '{args.alias}')")
    ui.warn("BACK UP this file and its password. If you lose them you can never publish updates to the same app.")
    ui.info(f"Use it: web2apk build ./app --release --keystore {path} --key-alias {args.alias}")
    return ExitCode.OK


def cmd_verify(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    apk = Path(args.apk).expanduser()
    if not apk.is_file():
        raise ConfigError(f"APK not found: {apk}")
    sdk = detect_sdk(gcfg.get("sdk_path"))
    bt = sdk.best_build_tools() if sdk.found else None
    signer = sdk.tool(bt, "apksigner") if bt else None
    if not signer:
        raise ToolchainError("apksigner not found.", hint="sdkmanager 'build-tools;35.0.0'")
    java = detect_java(gcfg.get("java_home"))
    p = subprocess.run(apksigner_verify_command(signer, apk), capture_output=True, text=True,
                       env=clean_env(java, sdk), stdin=subprocess.DEVNULL)
    ui.info((p.stdout + p.stderr).strip())
    if p.returncode != 0:
        raise SigningError("Signature verification FAILED.")
    ui.ok("Signature verified")
    return ExitCode.OK


def cmd_clean(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    project_dir = _project_dir(args.project)
    root = project_dir / ".web2apk"
    targets = [root / "build"]
    if args.logs:
        targets.append(root / "logs")
    if args.all:
        targets.append(root / "out")
    removed = 0
    for t in targets:
        if not t.exists():
            continue
        if t.is_symlink() or not is_within(t, root) or root.is_symlink():
            raise ConfigError(f"Refusing to delete suspicious path: {t}")
        if t.name == "build" and not (t / "android" / BUILD_MARKER).exists():
            raise ConfigError(f"{t} was not created by Web2APK (marker missing); refusing to delete it.")
        if args.dry_run:
            ui.info(f"would remove {t}")
        else:
            shutil.rmtree(t)
            ui.ok(f"removed {t}")
        removed += 1
    if not removed:
        ui.info("Nothing to clean.")
    return ExitCode.OK


def cmd_version(args, ui: Ui, gcfg: GlobalConfig, interactive: bool) -> int:
    print(f"web2apk {__version__}")
    return ExitCode.OK


COMMANDS = {"init": cmd_init, "build": cmd_build, "doctor": cmd_doctor, "config": cmd_config,
            "keystore": cmd_keystore, "verify": cmd_verify, "clean": cmd_clean, "version": cmd_version}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ui = Ui(quiet=getattr(args, "quiet", False), verbose=getattr(args, "verbose", False),
            color=False if getattr(args, "no_color", False) else None)
    interactive = sys.stdin.isatty() and not getattr(args, "no_interaction", False)
    try:
        gcfg = GlobalConfig.load()
        return int(COMMANDS[args.command](args, ui, gcfg, interactive))
    except Web2ApkError as exc:
        ui.error(str(exc), exc.hint)
        return int(exc.code)
    except KeyboardInterrupt:
        ui.error("Interrupted.")
        return 130
    except Exception as exc:  # last resort: never show a bare traceback unless asked
        if getattr(args, "verbose", False):
            raise
        ui.error(f"Unexpected error: {exc}", "Re-run with --verbose for a traceback, and check the build log.")
        return int(ExitCode.ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
