"""`web2apk doctor`: check the toolchain and explain how to fix problems."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional

from .android_sdk import detect_gradle, detect_java, detect_sdk
from .builder import MIN_JAVA, gradle_supported
from .config import GlobalConfig
from .signing import ENV_KEYSTORE, ENV_STORE_PASS, keystore_permission_warning
from .utils import ExitCode, Ui


@dataclass
class Check:
    name: str
    status: str   # ok | warn | fail
    detail: str
    fix: Optional[str] = None


def run_checks(gcfg: GlobalConfig, env: Optional[Mapping[str, str]] = None) -> List[Check]:
    env = os.environ if env is None else env
    out: List[Check] = []
    add = lambda *a, **k: out.append(Check(*a, **k))

    add("Python", "ok" if sys.version_info >= (3, 9) else "fail", sys.version.split()[0],
        None if sys.version_info >= (3, 9) else "Python 3.9+ is required")

    java = detect_java(gcfg.get("java_home"), env)
    if not java.found:
        add("Java", "fail", "no JDK found", "sudo apt install openjdk-21-jdk   (then: web2apk config set java_home /usr/lib/jvm/java-21-openjdk-amd64)")
    elif (java.major or 0) < MIN_JAVA:
        add("Java", "fail", f"JDK {java.major} at {java.home} (need {MIN_JAVA}+)", "sudo apt install openjdk-21-jdk; point java_home to it")
    else:
        add("Java", "ok" if (java.major or 0) in (17, 21) else "warn", f"JDK {java.major} at {java.home} (via {java.source})",
            None if (java.major or 0) in (17, 21) else "JDK 17 or 21 is recommended for AGP 8.7")
        add("keytool", "ok" if java.keytool else "fail", str(java.keytool or "missing"),
            None if java.keytool else "Install the full JDK (openjdk-21-jdk), not just the JRE")

    sdk = detect_sdk(gcfg.get("sdk_path"), env)
    if not sdk.found:
        add("Android SDK", "fail", "not found; looked in: " + ", ".join(sdk.tried),
            "Install the SDK command-line tools, then e.g.:\n  sdkmanager --sdk_root=/opt/android-sdk 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'\n"
            "or point to an existing SDK: web2apk config set sdk_path /path/to/sdk")
    else:
        add("Android SDK", "ok", f"{sdk.root} (via {sdk.source})")
        cs = gcfg.compile_sdk
        add(f"Platform android-{cs}", "ok" if sdk.has_platform(cs) else "fail",
            f"installed platforms: {sdk.platforms or 'none'}",
            None if sdk.has_platform(cs) else f"sdkmanager 'platforms;android-{cs}'")
        for lvl in (33, 34):
            if not sdk.has_platform(lvl):
                add(f"Platform android-{lvl}", "warn", "not installed (optional; compileSdk is "
                    f"{cs}, targetSdk may still be {lvl})")
        bt = sdk.best_build_tools()
        add("Build-tools", "ok" if bt else "fail", f"{bt} (apksigner + zipalign present)" if bt else f"installed: {sdk.build_tools or 'none'}",
            None if bt else "sdkmanager 'build-tools;35.0.0'")
        add("adb", "ok" if sdk.adb else "warn", str(sdk.adb or "platform-tools not installed (only needed to install on a device)"),
            None if sdk.adb else "sdkmanager 'platform-tools'  or  sudo apt install adb")
        add("sdkmanager", "ok" if sdk.sdkmanager else "warn", str(sdk.sdkmanager or "cmdline-tools not installed (only needed to install SDK packages)"))
        add("SDK licences", "ok" if sdk.licenses_accepted else "warn",
            "accepted" if sdk.licenses_accepted else "android-sdk-license not found",
            None if sdk.licenses_accepted else f"{sdk.sdkmanager or 'sdkmanager'} --licenses")
        if not os.access(sdk.root, os.W_OK):
            add("SDK writable", "warn", f"{sdk.root} is not writable by you",
                "Fine as long as every required package is already installed. Gradle cannot auto-install into it.")

    gradle = detect_gradle(gcfg.get("gradle_path"), env, java.home)
    if not gradle.found:
        add("Gradle", "warn", "no system Gradle found", "A wrapper can be used, but bootstrapping it needs one Gradle once: sudo apt install gradle")
    else:
        ok, why = gradle_supported(gcfg.agp_version, gradle.version or "0")
        add("Gradle", "ok" if ok is not False else "warn", f"{gradle.version} at {gradle.path} - {why}",
            None if ok is not False else "Web2APK will fall back to a Gradle wrapper (--gradle-mode wrapper)")
    add("Toolchain pins", "ok", f"AGP {gcfg.agp_version}, Kotlin {gcfg.kotlin_version}, wrapper Gradle {gcfg.gradle_version}, compileSdk {gcfg.compile_sdk}")

    try:
        import PIL  # noqa: F401
        add("Pillow", "ok", "available (custom --icon supported)")
    except ImportError:
        add("Pillow", "warn", "missing: custom --icon needs it; the built-in default icon works without",
            "pip install --user pillow   or   sudo apt install python3-pil")

    ks = env.get(ENV_KEYSTORE) or gcfg.get("keystore")
    if ks:
        p = Path(ks).expanduser()
        if not p.is_file():
            add("Release keystore", "fail", f"{p} does not exist", "web2apk keystore create " + str(p))
        else:
            w = keystore_permission_warning(p)
            add("Release keystore", "warn" if w else "ok", w or str(p), "chmod 600 " + str(p) if w else None)
        add("Keystore password", "ok" if env.get(ENV_STORE_PASS) else "warn",
            f"{ENV_STORE_PASS} is set" if env.get(ENV_STORE_PASS) else f"{ENV_STORE_PASS} not set (you will be prompted)")
    else:
        add("Release keystore", "warn", "none configured (debug builds work without one)",
            "web2apk keystore create ~/keys/release.p12 --alias mykey")
    return out


def render_checks(ui: Ui, checks: List[Check]) -> ExitCode:
    colors = {"ok": "green", "warn": "yellow", "fail": "red"}
    labels = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    for c in checks:
        ui.info(f"{ui.paint(colors[c.status], labels[c.status])} {c.name}: {c.detail}")
        if c.fix and c.status != "ok":
            for line in c.fix.splitlines():
                ui.info(f"       {ui.paint('cyan', '->')} {line}")
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    if fails:
        ui.error(f"{len(fails)} blocking problem(s), {len(warns)} warning(s).")
        return ExitCode.ENVIRONMENT
    ui.info(f"\nEnvironment looks good ({len(warns)} warning(s)).")
    return ExitCode.OK
