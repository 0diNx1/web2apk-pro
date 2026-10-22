"""Detect Java, the Android SDK and Gradle. Detection is read-only."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple


def version_tuple(text: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text.split("-")[0])) or (0,)


# --------------------------------------------------------------------------- Java

@dataclass
class JavaInfo:
    home: Optional[Path]
    java: Optional[Path] = None
    major: Optional[int] = None
    source: str = ""
    keytool: Optional[Path] = None

    @property
    def found(self) -> bool:
        return self.java is not None


def parse_java_major(text: str) -> Optional[int]:
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def java_major_version(java: Path) -> Optional[int]:
    try:
        p = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_java_major(p.stderr + p.stdout)


def detect_java(configured: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> JavaInfo:
    env = os.environ if env is None else env
    cands: List[Tuple[str, Path]] = []
    if configured:
        cands.append(("config", Path(configured).expanduser()))
    if env.get("JAVA_HOME"):
        cands.append(("JAVA_HOME", Path(env["JAVA_HOME"])))
    which = shutil.which("java", path=env.get("PATH"))
    if which:
        cands.append(("PATH", Path(os.path.realpath(which)).parent.parent))
    jvm = Path("/usr/lib/jvm")
    if jvm.is_dir():
        for p in sorted(jvm.iterdir(), reverse=True):
            if p.is_dir() and not p.is_symlink():
                cands.append(("/usr/lib/jvm", p))
    best: Optional[JavaInfo] = None
    for source, home in cands:
        java = home / "bin" / "java"
        if not (java.is_file() and os.access(java, os.X_OK)):
            continue
        keytool = home / "bin" / "keytool"
        info = JavaInfo(home, java, java_major_version(java), source, keytool if keytool.is_file() else None)
        if source == "config" or (info.major or 0) >= 17:
            return info
        best = best or info
    return best or JavaInfo(None)


# --------------------------------------------------------------------------- Android SDK

@dataclass
class SdkInfo:
    root: Optional[Path]
    source: str = ""
    platforms: List[int] = field(default_factory=list)
    build_tools: List[str] = field(default_factory=list)
    tried: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.root is not None

    def has_platform(self, level: int) -> bool:
        return level in self.platforms

    def best_build_tools(self, minimum: str = "34.0.0") -> Optional[str]:
        usable = [b for b in self.build_tools if (self.tool(b, "apksigner") and self.tool(b, "zipalign"))]
        good = [b for b in usable if version_tuple(b) >= version_tuple(minimum)]
        pool = good or usable
        return pool[-1] if pool else None

    def tool(self, build_tools_version: str, name: str) -> Optional[Path]:
        if not self.root:
            return None
        p = self.root / "build-tools" / build_tools_version / name
        return p if p.is_file() else None

    @property
    def adb(self) -> Optional[Path]:
        p = self.root / "platform-tools" / "adb" if self.root else None
        return p if p and p.is_file() else None

    @property
    def sdkmanager(self) -> Optional[Path]:
        if not self.root:
            return None
        for sub in ("cmdline-tools/latest/bin", "cmdline-tools/bin", "tools/bin"):
            p = self.root / sub / "sdkmanager"
            if p.is_file():
                return p
        return None

    @property
    def licenses_accepted(self) -> bool:
        return bool(self.root and (self.root / "licenses" / "android-sdk-license").is_file())


def _looks_like_sdk(p: Path) -> bool:
    return p.is_dir() and any((p / d).is_dir() for d in ("platforms", "build-tools", "platform-tools"))


def detect_sdk(configured: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> SdkInfo:
    env = os.environ if env is None else env
    cands: List[Tuple[str, Path]] = []
    if configured:
        cands.append(("config", Path(configured).expanduser()))
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        if env.get(var):
            cands.append((var, Path(env[var])))
    for p in ("/opt/android-sdk", "~/Android/Sdk", "/usr/lib/android-sdk", "/usr/local/lib/android/sdk"):
        cands.append(("default location", Path(p).expanduser()))
    tried = []
    for source, p in cands:
        tried.append(str(p))
        if _looks_like_sdk(p):
            platforms = sorted(int(m.group(1)) for d in (p / "platforms").glob("android-*")
                               if (m := re.fullmatch(r"android-(\d+)", d.name)))
            bt = sorted((d.name for d in (p / "build-tools").glob("*") if d.is_dir()), key=version_tuple)
            return SdkInfo(p.resolve(), source, platforms, bt, tried)
    return SdkInfo(None, "", [], [], tried)


# --------------------------------------------------------------------------- Gradle

@dataclass
class GradleInfo:
    path: Optional[Path]
    version: Optional[str] = None
    source: str = ""

    @property
    def found(self) -> bool:
        return self.path is not None


def parse_gradle_version(text: str) -> Optional[str]:
    m = re.search(r"^Gradle (\d+(?:\.\d+)*(?:-[\w.]+)?)", text, re.M)
    return m.group(1) if m else None


def detect_gradle(configured: Optional[str] = None, env: Optional[Mapping[str, str]] = None,
                  java_home: Optional[Path] = None) -> GradleInfo:
    env = dict(os.environ if env is None else env)
    cands: List[Tuple[str, Path]] = []
    if configured:
        p = Path(configured).expanduser()
        cands.append(("config", p / "bin" / "gradle" if p.is_dir() else p))
    if env.get("GRADLE_HOME"):
        cands.append(("GRADLE_HOME", Path(env["GRADLE_HOME"]) / "bin" / "gradle"))
    which = shutil.which("gradle", path=env.get("PATH"))
    if which:
        cands.append(("PATH", Path(which)))
    opt = Path("/opt/gradle")
    if opt.is_dir():
        for d in sorted(opt.iterdir(), key=lambda x: version_tuple(x.name), reverse=True):
            cands.append(("/opt/gradle", d / "bin" / "gradle"))
    cands.append(("/usr/share/gradle", Path("/usr/share/gradle/bin/gradle")))
    if java_home:
        env["JAVA_HOME"] = str(java_home)
    for source, exe in cands:
        if exe.is_file() and os.access(exe, os.X_OK):
            try:
                p = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=120, env=env,
                                   stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                continue
            ver = parse_gradle_version(p.stdout)
            if ver:
                return GradleInfo(exe, ver, source)
    return GradleInfo(None)
