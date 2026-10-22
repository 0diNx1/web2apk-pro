"""Generate the Android project, run Gradle, then align/sign/verify the APK."""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from .android_sdk import (GradleInfo, JavaInfo, SdkInfo, detect_gradle, detect_java, detect_sdk,
                          version_tuple)
from .config import GlobalConfig, ProjectConfig, cache_dir
from .project import WebProject, inspect_project, sync_assets
from .signing import (SigningSpec, apksigner_sign_command, apksigner_verify_command, resolve_signing,
                      zipalign_command, keystore_permission_warning)
from .utils import (BuildError, ConfigError, Ui, ToolchainError, SigningError, android_string, file_sha256,
                    hex_to_rgb, human_size, is_within, kotlin_string, make_png, properties_escape,
                    slugify, write_if_changed, Web2ApkError)

TEMPLATE_ROOT = Path(__file__).parent / "templates" / "android"
BUILD_MARKER = ".web2apk-build"
DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
MIN_JAVA = 17

# Minimum Gradle per Android Gradle Plugin series (from the AGP release notes; see README "Compatibility").
AGP_MIN_GRADLE = {"8.7": "8.9", "8.8": "8.10.2", "8.9": "8.11.1", "8.10": "8.11.1",
                  "8.11": "8.13", "8.12": "8.13", "8.13": "8.13"}

_FAILURE_HINTS: List[Tuple[str, str]] = [
    (r"SDK location not found", "Gradle cannot find the Android SDK. Run `web2apk doctor`; set it with `web2apk config set sdk_path /opt/android-sdk`."),
    (r"licen[sc]e.*not been accepted|Failed to install the following Android SDK packages|licenses? for package",
     "SDK licences are not accepted. Run: $ANDROID_SDK/cmdline-tools/latest/bin/sdkmanager --licenses  "
     "(use the SDK path from `web2apk doctor`)."),
    (r"Failed to find Build Tools revision|Failed to find target with hash string",
     "A required SDK package is missing. Install it with sdkmanager, e.g. "
     "sdkmanager 'platforms;android-35' 'build-tools;35.0.0'. If the SDK dir is root-owned, AGP cannot auto-install packages into it."),
    (r"Could not resolve|Could not GET|UnknownHostException|Connection (refused|reset)|Read timed out|PKIX path",
     "Gradle could not download dependencies. The first build needs network access to Google Maven/Maven Central "
     "(check connectivity, proxy, system clock). After one successful build you can use --offline."),
    (r"Minimum supported Gradle version|requires Gradle \d",
     "Your Gradle is older than the Android Gradle Plugin needs. Use --gradle-mode wrapper (pins a compatible Gradle)."),
    (r"Unsupported class file major version|requires Java \d+|Android Gradle plugin requires Java|"
     r"Could not determine java version|Unsupported Java",
     "Java version mismatch. AGP 8.x needs JDK 17+ (21 is fine). Set it with `web2apk config set java_home /usr/lib/jvm/java-21-openjdk-amd64`."),
    (r"AAPT2|aapt2", "AAPT2 failed. Check the SDK build-tools install, 64-bit support, and that /tmp is not mounted noexec."),
    (r"OutOfMemoryError|Java heap space|GC overhead", "Out of memory: raise org.gradle.jvmargs in the generated gradle.properties template."),
    (r"No space left on device", "Disk is full (Gradle caches live in ~/.gradle)."),
]


# --------------------------------------------------------------------------- compatibility

def agp_series(version: str) -> str:
    m = re.match(r"(\d+)\.(\d+)", version)
    return f"{m.group(1)}.{m.group(2)}" if m else version


def gradle_supported(agp: str, gradle: str) -> Tuple[Optional[bool], str]:
    need = AGP_MIN_GRADLE.get(agp_series(agp))
    if need is None:
        return None, f"AGP {agp} is not in the compatibility table; not checked"
    gv = version_tuple(gradle)
    if gv < version_tuple(need):
        return False, f"AGP {agp} needs Gradle >= {need}, found {gradle}"
    if gv[0] >= 9:
        return False, f"AGP {agp} does not support Gradle {gradle}"
    return True, f"Gradle {gradle} satisfies AGP {agp} (needs >= {need})"


def choose_gradle(gradle: GradleInfo, agp: str, mode: str) -> Tuple[str, str]:
    """Return ("system"|"wrapper", reason)."""
    if mode == "system":
        if not gradle.found:
            raise ToolchainError("gradle_mode=system but no Gradle was found.",
                                 hint="Install Gradle or set gradle_path (`web2apk config set gradle_path /opt/gradle/gradle-8.14.3`).")
        return "system", "forced by gradle_mode=system"
    if mode == "wrapper":
        return "wrapper", "forced by gradle_mode=wrapper"
    if gradle.found and gradle.version:
        ok, why = gradle_supported(agp, gradle.version)
        if ok is not False:
            return "system", f"system Gradle {gradle.version} ({why})"
        return "wrapper", why
    return "wrapper", "no system Gradle found"


def diagnose_failure(log_text: str) -> List[str]:
    hints = []
    for pattern, hint in _FAILURE_HINTS:
        if re.search(pattern, log_text, re.I) and hint not in hints:
            hints.append(hint)
    return hints


def gradle_command(gradle_exe: Path, build_dir: Path, release: bool, offline: bool = False,
                   stacktrace: bool = False) -> List[str]:
    cmd = [str(gradle_exe), "-p", str(build_dir), "--console=plain", "--warning-mode=summary"]
    if offline:
        cmd.append("--offline")
    if stacktrace:
        cmd.append("--stacktrace")
    cmd.append(":app:assembleRelease" if release else ":app:assembleDebug")
    return cmd


# --------------------------------------------------------------------------- template rendering

_PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")


def render(text: str, values: Mapping[str, str]) -> str:
    def sub(m):
        key = m.group(1)
        if key not in values:
            raise BuildError(f"Template placeholder @@{key}@@ has no value (internal error)")
        return values[key]
    return _PLACEHOLDER.sub(sub, text)


def kt_bool(b: bool) -> str:
    return "true" if b else "false"


def manifest_permissions(cfg: ProjectConfig) -> str:
    lines = []
    if cfg.internet_access:
        lines += ['    <uses-permission android:name="android.permission.INTERNET" />',
                  '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />']
    if "camera" in cfg.permissions:
        lines += ['    <uses-permission android:name="android.permission.CAMERA" />',
                  '    <uses-feature android:name="android.hardware.camera" android:required="false" />']
    if "microphone" in cfg.permissions:
        lines += ['    <uses-permission android:name="android.permission.RECORD_AUDIO" />']
    if "location" in cfg.permissions:
        lines += ['    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />',
                  '    <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />',
                  '    <uses-feature android:name="android.hardware.location" android:required="false" />']
    return "\n".join(lines)


def template_values(cfg: ProjectConfig, web: WebProject, sdk_root: Path, build_tools: str,
                    gcfg: GlobalConfig) -> Dict[str, str]:
    hosts = ", ".join(kotlin_string(h) for h in cfg.allowed_origins)
    orientation = {"auto": "unspecified", "portrait": "portrait", "landscape": "landscape"}[cfg.orientation]
    return {
        "PACKAGE_ID": cfg.package_id,
        "PROJECT_NAME": slugify(cfg.app_name),
        "AGP_VERSION": gcfg.agp_version,
        "KOTLIN_VERSION": gcfg.kotlin_version,
        "COMPILE_SDK": str(gcfg.compile_sdk),
        "BUILD_TOOLS": build_tools,
        "MIN_SDK": str(cfg.min_sdk),
        "TARGET_SDK": str(cfg.target_sdk),
        "VERSION_CODE": str(cfg.version_code),
        "VERSION_NAME": cfg.version_name,
        "CLEARTEXT": kt_bool(cfg.allow_cleartext and cfg.internet_access),
        "ORIENTATION": orientation,
        "MANIFEST_PERMISSIONS": manifest_permissions(cfg),
        "APP_NAME_XML": android_string(cfg.app_name.strip()),
        "SPLASH_COLOR": cfg.splash_color,
        "KT_APP_NAME": kotlin_string(cfg.app_name.strip()),
        "KT_START_URL": kotlin_string("https://appassets.androidplatform.net/assets/www/"
                                      + quote(web.entry_point, safe="/")),
        "KT_INTERNET": kt_bool(cfg.internet_access),
        "KT_CLEARTEXT": kt_bool(cfg.allow_cleartext and cfg.internet_access),
        "KT_FULLSCREEN": kt_bool(cfg.fullscreen),
        "KT_UPLOADS": kt_bool(cfg.allow_file_uploads),
        "KT_DOWNLOADS": kt_bool(cfg.allow_downloads and cfg.internet_access),
        "KT_BRIDGE": kt_bool(cfg.js_bridge),
        "KT_DEBUG_WEBVIEW": kt_bool(cfg.debug_webview),
        "KT_CAMERA": kt_bool("camera" in cfg.permissions),
        "KT_MICROPHONE": kt_bool("microphone" in cfg.permissions),
        "KT_LOCATION": kt_bool("location" in cfg.permissions),
        "KT_STATUS_COLOR": kotlin_string(cfg.status_bar_color),
        "KT_ALLOWED_HOSTS": f"listOf({hosts})" if hosts else "emptyList()",
    }


# --------------------------------------------------------------------------- icons

def _icon_pngs(cfg: ProjectConfig, project_dir: Path) -> Dict[str, bytes]:
    if cfg.icon:
        icon = Path(cfg.icon).expanduser()
        if not icon.is_absolute():
            icon = project_dir / icon
        if not icon.is_file():
            raise ConfigError(f"Icon not found: {icon}")
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            raise ToolchainError("Pillow is required to resize a custom icon.",
                                 hint="Install it for your user: pip install --user pillow   "
                                      "(or: sudo apt install python3-pil)")
        import io
        try:
            img = Image.open(icon).convert("RGBA")
        except Exception as exc:  # PIL raises many types
            raise ConfigError(f"Cannot read icon {icon}: {exc}") from exc
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        out = {}
        for name, px in DENSITIES.items():
            buf = io.BytesIO()
            square.resize((px, px), Image.LANCZOS).save(buf, "PNG")
            out[name] = buf.getvalue()
        return out
    bg = hex_to_rgb(cfg.splash_color)
    return {name: make_png(px, bg, (255, 255, 255)) for name, px in DENSITIES.items()}


# --------------------------------------------------------------------------- project generation

def ensure_build_dir(build_dir: Path) -> None:
    marker = build_dir / BUILD_MARKER
    if build_dir.exists() and not marker.exists() and any(build_dir.iterdir()):
        raise BuildError(f"{build_dir} exists but was not created by Web2APK; refusing to use it.")
    build_dir.mkdir(parents=True, exist_ok=True)
    write_if_changed(marker, "Created by Web2APK Pro. Safe to delete with `web2apk clean`.\n")


def _prune(root: Path, keep: set) -> None:
    if not root.is_dir():
        return
    for dirpath, _dirs, files in os.walk(root, topdown=False):
        for n in files:
            p = Path(dirpath, n)
            if p.resolve() not in keep:
                p.unlink()
        if Path(dirpath) != root and not any(Path(dirpath).iterdir()):
            Path(dirpath).rmdir()


def generate_project(cfg: ProjectConfig, web: WebProject, sdk: SdkInfo, build_tools: str,
                     gcfg: GlobalConfig, build_dir: Path, ui: Ui) -> Dict[str, int]:
    ensure_build_dir(build_dir)
    values = template_values(cfg, web, sdk.root, build_tools, gcfg)
    pkg_path = Path(*cfg.package_id.split("."))
    written = changed = 0
    kept_java: set = set()
    kept_res: set = set()

    for src in sorted(TEMPLATE_ROOT.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(TEMPLATE_ROOT)
        parts = []
        for part in rel.parts:
            parts.extend(pkg_path.parts if part == "__PACKAGE_PATH__" else [part])
        dest_rel = Path(*parts)
        data = src.read_text(encoding="utf-8")
        if dest_rel.suffix == ".tpl":
            data = render(data, values)
            dest_rel = dest_rel.with_suffix("")
        dest = build_dir / dest_rel
        if not is_within(dest, build_dir):
            raise BuildError(f"Template path escapes the build directory: {dest_rel}")
        changed += write_if_changed(dest, data)
        written += 1
        rp = dest.resolve()
        s = dest_rel.as_posix()
        if s.startswith("app/src/main/java/"):
            kept_java.add(rp)
        elif s.startswith("app/src/main/res/"):
            kept_res.add(rp)

    for density, png in _icon_pngs(cfg, web.project_dir).items():
        dest = build_dir / "app/src/main/res" / f"mipmap-{density}" / "ic_launcher.png"
        changed += write_if_changed(dest, png)
        kept_res.add(dest.resolve())
        written += 1

    sdk_line = f"sdk.dir={properties_escape(str(sdk.root))}\n"
    changed += write_if_changed(build_dir / "local.properties", sdk_line)

    _prune(build_dir / "app/src/main/java", kept_java)
    _prune(build_dir / "app/src/main/res", kept_res)

    copied, unchanged, removed = sync_assets(web.files, build_dir / "app/src/main/assets/www")
    ui.debug(f"template files: {written} ({changed} changed); assets: {copied} copied, {unchanged} unchanged, {removed} removed")
    return {"files": written, "changed": changed, "assets_copied": copied, "assets_unchanged": unchanged,
            "assets_removed": removed}


# --------------------------------------------------------------------------- wrapper

def ensure_wrapper(gradle: GradleInfo, version: str, env: Mapping[str, str], dest_project: Path, ui: Ui) -> Path:
    """Create (or reuse) a Gradle wrapper for `version` and copy it into the generated project.

    `gradle wrapper` is run in a scratch project with no plugins so it works offline-of-AGP; the
    wrapper then downloads the real distribution on first use unless it is already in ~/.gradle.
    """
    cache = cache_dir() / f"wrapper-{version}"
    if not (cache / "gradlew").is_file():
        if not gradle.found:
            raise ToolchainError("Need an installed Gradle once to bootstrap the wrapper.",
                                 hint="Install Gradle (e.g. sudo apt install gradle) or set gradle_path.")
        cache.mkdir(parents=True, exist_ok=True)
        scratch = cache / "scratch"
        scratch.mkdir(exist_ok=True)
        (scratch / "settings.gradle").write_text("rootProject.name = 'wrapper-bootstrap'\n")
        p = subprocess.run([str(gradle.path), "wrapper", "--gradle-version", version, "--distribution-type", "bin"],
                           cwd=scratch, env=dict(env), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise BuildError("Could not generate the Gradle wrapper:\n" + (p.stderr or p.stdout)[-1500:],
                             hint="Check network access to services.gradle.org, or use --gradle-mode system.")
        for name in ("gradlew", "gradlew.bat"):
            shutil.copy2(scratch / name, cache / name)
        (cache / "gradle" / "wrapper").mkdir(parents=True, exist_ok=True)
        for name in ("gradle-wrapper.jar", "gradle-wrapper.properties"):
            shutil.copy2(scratch / "gradle" / "wrapper" / name, cache / "gradle" / "wrapper" / name)
        shutil.rmtree(scratch)
    for name in ("gradlew", "gradlew.bat"):
        shutil.copy2(cache / name, dest_project / name)
    (dest_project / "gradle" / "wrapper").mkdir(parents=True, exist_ok=True)
    for name in ("gradle-wrapper.jar", "gradle-wrapper.properties"):
        shutil.copy2(cache / "gradle" / "wrapper" / name, dest_project / "gradle" / "wrapper" / name)
    (dest_project / "gradlew").chmod(0o755)
    return dest_project / "gradlew"


# --------------------------------------------------------------------------- running tools

@dataclass
class BuildRequest:
    project_dir: Path
    cfg: ProjectConfig
    gcfg: GlobalConfig
    release: bool = False
    output_dir: Optional[Path] = None
    dry_run: bool = False
    offline: bool = False
    force: bool = False
    interactive: bool = True
    gradle_mode: Optional[str] = None
    keystore: Optional[str] = None
    key_alias: Optional[str] = None
    verbose_gradle: bool = False


@dataclass
class BuildResult:
    apk: Optional[Path]
    package_id: str
    version_name: str
    version_code: int
    variant: str
    size: int = 0
    sha256: str = ""
    signed_with: str = ""
    log_file: Optional[Path] = None
    dry_run: bool = False


def clean_env(java: JavaInfo, sdk: SdkInfo, base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not (k.startswith("WEB2APK_") and ("PASSWORD" in k))}
    if java.home:
        env["JAVA_HOME"] = str(java.home)
        env["PATH"] = f"{java.home}/bin{os.pathsep}{env.get('PATH', '')}"
    if sdk.root:
        env["ANDROID_HOME"] = env["ANDROID_SDK_ROOT"] = str(sdk.root)
    return env


def _run_logged(argv: Sequence[str], cwd: Optional[Path], env: Mapping[str, str], log, ui: Ui,
                spinner=None, echo: bool = False) -> Tuple[int, str]:
    tail: List[str] = []
    proc = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        log.write(line)
        tail.append(line)
        if len(tail) > 4000:
            del tail[:2000]
        m = re.match(r"> Task (:\S+)", line)
        if m and spinner:
            spinner.update(m.group(1))
        if echo:
            ui.info(line.rstrip())
    return proc.wait(), "".join(tail)


def output_name(cfg: ProjectConfig, release: bool) -> str:
    return f"{slugify(cfg.app_name)}-{cfg.version_name}-{'release' if release else 'debug'}.apk"


def run_build(req: BuildRequest, ui: Ui, env: Optional[Mapping[str, str]] = None) -> BuildResult:
    env = os.environ if env is None else env
    cfg, gcfg = req.cfg, req.gcfg
    variant = "release" if req.release else "debug"
    paths = {"root": req.project_dir / ".web2apk"}
    paths.update(build=paths["root"] / "build" / "android", logs=paths["root"] / "logs", out=paths["root"] / "out")
    out_dir = (req.output_dir or paths["out"]).expanduser().resolve()
    apk_name = output_name(cfg, req.release)
    final_apk = out_dir / apk_name
    total = 6 if req.release else 5
    result = BuildResult(final_apk, cfg.package_id, cfg.version_name, cfg.version_code, variant, dry_run=req.dry_run)

    # 1 -- environment -------------------------------------------------------------------------
    ui.step(1, total, "Checking toolchain")
    java = detect_java(gcfg.get("java_home"), env)
    sdk = detect_sdk(gcfg.get("sdk_path"), env)
    gradle = detect_gradle(gcfg.get("gradle_path"), env, java.home)
    problems: List[Tuple[str, str]] = []
    if not java.found:
        problems.append(("Java (JDK) not found", "Install: sudo apt install openjdk-21-jdk"))
    elif (java.major or 0) < MIN_JAVA:
        problems.append((f"Java {java.major} is too old (need {MIN_JAVA}+)", "Install openjdk-21-jdk and set java_home"))
    if not sdk.found:
        problems.append(("Android SDK not found", "Set it: web2apk config set sdk_path /opt/android-sdk"))
    else:
        if not sdk.has_platform(gcfg.compile_sdk):
            problems.append((f"SDK platform android-{gcfg.compile_sdk} missing",
                             f"sdkmanager 'platforms;android-{gcfg.compile_sdk}'"))
        if not sdk.best_build_tools():
            problems.append(("No usable build-tools (need apksigner + zipalign)", "sdkmanager 'build-tools;35.0.0'"))
    mode = req.gradle_mode or gcfg.gradle_mode
    try:
        chosen, why = choose_gradle(gradle, gcfg.agp_version, mode)
    except ToolchainError as exc:
        problems.append((str(exc), exc.hint or ""))
        chosen, why = "system", "unavailable"
    if chosen == "wrapper" and not gradle.found and not (cache_dir() / f"wrapper-{gcfg.gradle_version}" / "gradlew").is_file():
        problems.append(("Gradle wrapper needed but no Gradle available to bootstrap it", "sudo apt install gradle"))
    if problems and not req.dry_run:
        raise ToolchainError("; ".join(p for p, _ in problems), hint="\n".join(h for _, h in problems if h) +
                             "\nRun `web2apk doctor` for a full report.")
    for p, h in problems:
        ui.warn(f"{p} ({h})")
    ui.info(f"  java {java.major or '?'} @ {java.home}; sdk {sdk.root}; gradle: {chosen} ({why})")

    # 2 -- project -----------------------------------------------------------------------------
    ui.step(2, total, "Reading web project")
    web = inspect_project(req.project_dir, cfg, exclude_paths=[paths["root"], out_dir])
    for w in web.warnings:
        ui.warn(w)
    ui.info(f"  web root: {web.web_root}  entry: {web.entry_point}  files: {len(web.files)}")
    if web.skipped:
        ui.debug("skipped: " + ", ".join(web.skipped[:20]))

    # release prechecks happen before the (slow) build
    spec: Optional[SigningSpec] = None
    if req.release:
        if final_apk.exists() and not req.force:
            raise BuildError(f"Refusing to overwrite existing release APK: {final_apk}",
                             hint="Use --force, bump version_name/version_code, or choose another --output-dir.")
        if not req.dry_run:
            spec = resolve_signing(req.keystore or gcfg.get("keystore"), req.key_alias or gcfg.get("key_alias"),
                                   env, req.interactive)
            warn = keystore_permission_warning(spec.keystore)
            if warn:
                ui.warn(warn)
        if cfg.package_id.startswith("com.example."):
            ui.warn("package_id uses the placeholder prefix com.example.; choose your own before publishing")

    build_tools = (sdk.best_build_tools() if sdk.found else None) or "35.0.0"
    wrapper_note = ""
    if req.dry_run:
        gexe = Path("gradlew") if chosen == "wrapper" else (gradle.path or Path("gradle"))
        cmd = gradle_command(gexe, paths["build"], req.release, req.offline)
        ui.info("\nDRY RUN - nothing was written or executed. Planned actions:")
        ui.info(f"  1. generate Android project in {paths['build']}")
        ui.info(f"  2. copy {len(web.files)} asset files ({web.total_size} bytes) -> app/src/main/assets/www")
        ui.info("  3. run: " + " ".join(cmd))
        if req.release:
            ui.info("  4. zipalign, sign with apksigner (keystore passwords via env/prompt), apksigner verify")
        ui.info(f"  -> {final_apk}")
        return result

    # 3 -- generate ----------------------------------------------------------------------------
    ui.step(3, total, "Generating Android project")
    stats = generate_project(cfg, web, sdk, build_tools, gcfg, paths["build"], ui)
    ui.info(f"  {stats['files']} template files ({stats['changed']} changed), assets: "
            f"{stats['assets_copied']} copied / {stats['assets_unchanged']} unchanged / {stats['assets_removed']} removed")

    # 4 -- gradle ------------------------------------------------------------------------------
    ui.step(4, total, f"Building {variant} with Gradle")
    genv = clean_env(java, sdk, env)
    if chosen == "wrapper":
        gexe = ensure_wrapper(gradle, gcfg.gradle_version, genv, paths["build"], ui)
    else:
        gexe = gradle.path
    apk_dir = paths["build"] / "app" / "build" / "outputs" / "apk" / variant
    if apk_dir.is_dir() and is_within(apk_dir, paths["build"]):
        shutil.rmtree(apk_dir)  # never pick up a stale APK
    paths["logs"].mkdir(parents=True, exist_ok=True)
    log_path = paths["logs"] / f"build-{datetime.datetime.now():%Y%m%d-%H%M%S}-{variant}.log"
    cmd = gradle_command(gexe, paths["build"], req.release, req.offline, stacktrace=ui.verbose)
    result.log_file = log_path
    _rotate_logs(paths["logs"])
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"web2apk build log\ncommand: {' '.join(cmd)}\njava_home: {java.home}\nsdk: {sdk.root}\n\n")
        log.flush()
        with ui.spinner("Gradle") as sp:
            code, tail = _run_logged(cmd, paths["build"], genv, log, ui, sp, echo=ui.verbose)
            if code != 0:
                sp.failed = True
    if code != 0:
        last = "".join(tail.splitlines(True)[-25:])
        hints = diagnose_failure(tail)
        raise BuildError(f"Gradle build failed (exit {code}). Last output:\n{last}",
                         hint=("\n".join(hints) + "\n" if hints else "") + f"Full log: {log_path}")
    produced = sorted(apk_dir.glob("*.apk"), key=lambda p: p.stat().st_mtime)
    if not produced:
        raise BuildError(f"Gradle succeeded but no APK was found in {apk_dir}", hint=f"See {log_path}")
    built = produced[-1]

    # 5 -- sign (release) ----------------------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    if req.release:
        ui.step(5, total, "Aligning and signing")
        assert spec is not None
        zipalign = sdk.tool(build_tools, "zipalign")
        apksigner = sdk.tool(build_tools, "apksigner")
        aligned = paths["build"] / "app" / "build" / "outputs" / f"{apk_name}.aligned"
        tmp_out = out_dir / (apk_name + ".tmp")
        for tmp in (aligned, tmp_out):
            tmp.unlink(missing_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:
            for argv, extra in ((zipalign_command(zipalign, built, aligned), {}),
                                (*apksigner_sign_command(apksigner, spec, aligned, tmp_out),)):
                senv = dict(genv)
                senv.update(extra)
                log.write("\n$ " + " ".join(argv) + "\n")
                code, out = _run_logged(argv, None, senv, log, ui)
                if code != 0:
                    tmp_out.unlink(missing_ok=True)
                    raise SigningError(f"{Path(argv[0]).name} failed:\n{out.strip()[-1200:]}",
                                       hint="Check the keystore path, alias and passwords.")
        aligned.unlink(missing_ok=True)
        os.replace(tmp_out, final_apk)
        ui.step(6, total, "Verifying signature")
        with open(log_path, "a", encoding="utf-8") as log:
            argv = apksigner_verify_command(apksigner, final_apk)
            log.write("\n$ " + " ".join(argv) + "\n")
            code, out = _run_logged(argv, None, genv, log, ui)
        if code != 0:
            raise SigningError(f"APK signature verification failed:\n{out.strip()[-800:]}")
        m = re.search(r"certificate SHA-256 digest: ([0-9a-f]+)", out)
        result.signed_with = f"release key (cert SHA-256 {m.group(1)[:16]}...)" if m else "release key"
    else:
        ui.step(5, total, "Collecting APK")
        shutil.copy2(built, final_apk)
        result.signed_with = "Android debug key (NOT for distribution)"
    result.size = final_apk.stat().st_size
    result.sha256 = file_sha256(final_apk)
    return result


def _rotate_logs(logs: Path, keep: int = 20) -> None:
    files = sorted(logs.glob("build-*.log"), key=lambda p: p.stat().st_mtime)
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


def print_summary(ui: Ui, res: BuildResult) -> None:
    ui.info("")
    ui.info(ui.paint("bold", "Build summary" + (" (dry run)" if res.dry_run else "")))
    rows = [("APK", str(res.apk)), ("Package ID", res.package_id),
            ("Version", f"{res.version_name} (code {res.version_code})"), ("Build type", res.variant)]
    if not res.dry_run:
        rows += [("Size", human_size(res.size)), ("SHA-256", res.sha256), ("Signed with", res.signed_with),
                 ("Log", str(res.log_file))]
    for k, val in rows:
        ui.info(f"  {k:<12} {val}")
