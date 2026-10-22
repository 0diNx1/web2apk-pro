"""Inspect a web project directory and copy its assets safely."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .config import ProjectConfig
from .utils import ConfigError, file_sha256, is_within, resolve_within

EXCLUDED_DIR_NAMES = {"node_modules", "__pycache__"}
EXCLUDED_FILE_NAMES = {"web2apk.json", "Thumbs.db", "desktop.ini"}
SENSITIVE_SUFFIXES = {".keystore", ".jks", ".p12", ".pfx", ".pem", ".key"}
AUTO_WEB_ROOTS = (".", "public", "assets", "www")
INDEX_CANDIDATES = ("index.html", "index.htm")
WARN_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass
class AssetFile:
    rel: str       # POSIX path relative to the web root
    abs: Path
    size: int


@dataclass
class WebProject:
    project_dir: Path
    web_root: Path
    entry_point: str
    files: List[AssetFile]
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


def _html_files(root: Path) -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in EXCLUDED_DIR_NAMES]
        for n in filenames:
            if n.lower().endswith((".html", ".htm")):
                out.append(Path(dirpath, n).relative_to(root).as_posix())
    return sorted(out)[:10]


def detect_web_root_and_entry(project_dir: Path, cfg: ProjectConfig) -> Tuple[Path, str]:
    if cfg.web_root != "auto":
        root = resolve_within(project_dir, cfg.web_root, "web_root")
        if not root.is_dir():
            raise ConfigError(f"web_root '{cfg.web_root}' is not a directory inside {project_dir}")
        roots = [root]
    else:
        roots = [resolve_within(project_dir, r, "web_root") for r in AUTO_WEB_ROOTS]
        roots = [r for r in roots if r.is_dir()]

    for root in roots:
        if cfg.entry_point != "auto":
            entry = resolve_within(root, cfg.entry_point, "entry_point")
            if entry.is_file():
                return root, entry.relative_to(root.resolve()).as_posix()
        else:
            for name in INDEX_CANDIDATES:
                if (root / name).is_file():
                    return root, name

    where = ", ".join(str(r.relative_to(project_dir.resolve())) if r != project_dir.resolve() else "." for r in roots) or "."
    if cfg.entry_point != "auto":
        found = _html_files(project_dir)
        raise ConfigError(
            f"Entry point '{cfg.entry_point}' not found (searched: {where}).",
            hint=("HTML files found: " + ", ".join(found) if found else "No .html files found in the project.")
            + "\nSet entry_point in web2apk.json or pass --entry.")
    found = _html_files(project_dir)
    raise ConfigError(
        f"No index.html found in {project_dir} (searched: {where}).",
        hint=("HTML files found: " + ", ".join(found) + "\nSet entry_point in web2apk.json or pass --entry."
              if found else "Add an index.html or create a starter project with: web2apk init <dir>"))


def collect_files(web_root: Path, exclude_paths: Iterable[Path] = ()) -> Tuple[List[AssetFile], List[str], List[str]]:
    """Walk the web root without following symlinks. Hidden files, VCS dirs, node_modules, keystores
    and symlinks are skipped. Returns (files, skipped descriptions, warnings)."""
    root = web_root.resolve()
    excl = [Path(p).resolve() for p in exclude_paths]
    files: List[AssetFile] = []
    skipped: List[str] = []
    warnings: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            full = here / d
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                skipped.append(f"{rel}/ (symlink)")
            elif d.startswith(".") or d in EXCLUDED_DIR_NAMES or any(is_within(full, e) for e in excl):
                skipped.append(f"{rel}/")
            else:
                keep.append(d)
        dirnames[:] = keep
        for n in sorted(filenames):
            full = here / n
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                skipped.append(f"{rel} (symlink)")
                continue
            if n.startswith(".") or n in EXCLUDED_FILE_NAMES:
                skipped.append(rel)
                continue
            if full.suffix.lower() in SENSITIVE_SUFFIXES:
                skipped.append(rel)
                warnings.append(f"'{rel}' looks like a key/certificate file and was NOT packaged into the APK")
                continue
            if not is_within(full, root):  # defence in depth
                skipped.append(f"{rel} (outside root)")
                continue
            files.append(AssetFile(rel, full, full.stat().st_size))
    return files, skipped, warnings


def scan_entry_html(entry: Path, cfg: ProjectConfig) -> List[str]:
    """Static text checks only; project code is never executed."""
    warns = []
    try:
        text = entry.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except OSError as exc:
        return [f"could not read entry point: {exc}"]
    if not re.search(r"<meta[^>]+name=[\"']viewport[\"']", text, re.I):
        warns.append('entry point has no <meta name="viewport" content="width=device-width, initial-scale=1"> tag; '
                     "layout will look like a desktop page")
    if re.search(r"""(?:src|href)\s*=\s*["']file://""", text, re.I):
        warns.append("entry point references file:// URLs, which are blocked; use relative paths")
    if re.search(r"""(?:src|href)\s*=\s*["']http://""", text, re.I) and not (cfg.internet_access and cfg.allow_cleartext):
        warns.append("entry point references http:// URLs, which are blocked unless internet_access and allow_cleartext are set")
    if re.search(r"""(?:src|href)\s*=\s*["']https://""", text, re.I) and not cfg.internet_access:
        warns.append("entry point references https:// resources but internet_access is false; they will not load")
    return warns


def inspect_project(project_dir: Path, cfg: ProjectConfig, exclude_paths: Iterable[Path] = ()) -> WebProject:
    if not project_dir.exists():
        raise ConfigError(f"Project directory not found: {project_dir}")
    if not project_dir.is_dir():
        raise ConfigError(f"Not a directory: {project_dir}")
    root, entry = detect_web_root_and_entry(project_dir, cfg)
    files, skipped, warnings = collect_files(root, exclude_paths)
    if not files:
        raise ConfigError(f"No packageable files found in {root}")
    warnings += scan_entry_html(root / entry, cfg)
    proj = WebProject(project_dir.resolve(), root, entry, files, warnings, skipped)
    if proj.total_size > WARN_TOTAL_BYTES:
        proj.warnings.append(f"web assets total {proj.total_size // (1024 * 1024)} MB; the APK will be large")
    return proj


def sync_assets(files: List[AssetFile], dest: Path) -> Tuple[int, int, int]:
    """Mirror `files` into `dest` (copy changed, remove stale). Returns (copied, unchanged, removed)."""
    dest.mkdir(parents=True, exist_ok=True)
    dest_r = dest.resolve()
    wanted = set()
    copied = unchanged = 0
    for f in files:
        target = resolve_within(dest_r, f.rel, "asset")
        wanted.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == f.size and file_sha256(target) == file_sha256(f.abs):
            unchanged += 1
            continue
        target.write_bytes(f.abs.read_bytes())
        copied += 1
    removed = 0
    for dirpath, dirnames, filenames in os.walk(dest_r, topdown=False):
        for n in filenames:
            p = Path(dirpath, n)
            if p.resolve() not in wanted and is_within(p, dest_r):
                p.unlink()
                removed += 1
        if Path(dirpath) != dest_r and not any(Path(dirpath).iterdir()):
            Path(dirpath).rmdir()
    return copied, unchanged, removed
