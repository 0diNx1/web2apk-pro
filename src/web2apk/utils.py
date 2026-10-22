"""Shared helpers: exit codes, errors, terminal UI, safe paths, small generators."""
from __future__ import annotations

import enum
import hashlib
import os
import re
import shutil
import struct
import sys
import threading
import time
import zlib
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple, Union


class ExitCode(enum.IntEnum):
    OK = 0
    ERROR = 1          # unexpected failure
    USAGE = 2          # bad command line (argparse uses 2 as well)
    CONFIG = 3         # invalid project / configuration / path
    ENVIRONMENT = 4    # missing or unsuitable toolchain (doctor failures)
    BUILD = 5          # Gradle / packaging failure
    SIGNING = 6        # keystore / signing / verification failure
    ABORTED = 7        # needed interaction but --no-interaction / user said no


class Web2ApkError(Exception):
    """Base error. `code` becomes the process exit status, `hint` is shown to the user."""

    def __init__(self, message: str, code: ExitCode = ExitCode.ERROR, hint: Optional[str] = None):
        super().__init__(message)
        self.code = ExitCode(code)
        self.hint = hint


class ConfigError(Web2ApkError):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message, ExitCode.CONFIG, hint)


class PathSafetyError(ConfigError):
    pass


class ToolchainError(Web2ApkError):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message, ExitCode.ENVIRONMENT, hint)


class BuildError(Web2ApkError):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message, ExitCode.BUILD, hint)


class SigningError(Web2ApkError):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message, ExitCode.SIGNING, hint)


class AbortedError(Web2ApkError):
    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message, ExitCode.ABORTED, hint)


# --------------------------------------------------------------------------- terminal UI

_ANSI = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36", "dim": "2", "bold": "1"}


class Ui:
    """Tiny terminal UI. Status goes to stderr; stdout is reserved for machine-readable output."""

    def __init__(self, quiet: bool = False, verbose: bool = False, color: Optional[bool] = None, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.verbose = verbose
        self.quiet = quiet and not verbose
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        if color is None:
            color = self.tty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.color = color

    def paint(self, name: str, text: str) -> str:
        if not self.color:
            return text
        return f"\033[{_ANSI[name]}m{text}\033[0m"

    def _emit(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def info(self, msg: str) -> None:
        if not self.quiet:
            self._emit(msg)

    def ok(self, msg: str) -> None:
        if not self.quiet:
            self._emit(f"{self.paint('green', 'OK')}  {msg}")

    def warn(self, msg: str) -> None:
        self._emit(f"{self.paint('yellow', 'WARN')} {msg}")

    def error(self, msg: str, hint: Optional[str] = None) -> None:
        self._emit(f"{self.paint('red', 'ERROR')} {msg}")
        if hint:
            for i, line in enumerate(hint.splitlines()):
                self._emit(f"      {self.paint('cyan', 'hint:') if i == 0 else '     '} {line}")

    def debug(self, msg: str) -> None:
        if self.verbose:
            self._emit(self.paint("dim", f"  {msg}"))

    def step(self, i: int, n: int, msg: str) -> None:
        if not self.quiet:
            self._emit(self.paint("bold", f"[{i}/{n}] {msg}"))

    def spinner(self, msg: str) -> "_Spinner":
        return _Spinner(self, msg)


class _Spinner:
    FRAMES = "|/-\\"

    def __init__(self, ui: Ui, msg: str):
        self.ui, self.msg, self.detail = ui, msg, ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0
        self.failed = False

    def __enter__(self) -> "_Spinner":
        self._t0 = time.monotonic()
        if self.ui.tty and not self.ui.quiet and not self.ui.verbose:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def update(self, detail: str) -> None:
        self.detail = detail

    def _run(self) -> None:
        i = 0
        while not self._stop.wait(0.12):
            width = shutil.get_terminal_size((80, 20)).columns
            line = f"  {self.FRAMES[i % 4]} {self.msg} {self.detail}"[: max(width - 1, 10)]
            self.ui.stream.write("\r" + self.ui.paint("dim", line) + "\033[K")
            self.ui.stream.flush()
            i += 1

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join()
            self.ui.stream.write("\r\033[K")
            self.ui.stream.flush()
        if exc_type is None and not self.failed:
            self.ui.ok(f"{self.msg} ({time.monotonic() - self._t0:.1f}s)")
        return False


# --------------------------------------------------------------------------- paths

def resolve_within(base: Path, rel: str, field: str = "path") -> Path:
    """Resolve `rel` under `base`, rejecting absolute paths, '..' and symlink escapes."""
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise PathSafetyError(f"{field}: empty or invalid path")
    norm = rel.replace("\\", "/")
    p = PurePosixPath(norm)
    if p.is_absolute() or re.match(r"^[A-Za-z]:", norm) or ".." in p.parts:
        raise PathSafetyError(f"{field}: '{rel}' must be a relative path without '..'")
    base_r = base.resolve()
    cand = (base_r.joinpath(*p.parts)).resolve() if p.parts else base_r
    if cand != base_r and base_r not in cand.parents:
        raise PathSafetyError(f"{field}: '{rel}' resolves outside of {base}")
    return cand


def is_within(child: Path, parent: Path) -> bool:
    c, p = child.resolve(), parent.resolve()
    return c == p or p in c.parents


def slugify(text: str, sep: str = "-", fallback: str = "app") -> str:
    s = re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)
    return s or fallback


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_if_changed(path: Path, data: Union[str, bytes], mode: Optional[int] = None) -> bool:
    """Write only when content differs (keeps mtimes stable so Gradle stays up to date)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
        if mode is not None:
            path.chmod(mode)
        return False
    path.write_bytes(data)
    if mode is not None:
        path.chmod(mode)
    return True


# --------------------------------------------------------------------------- escaping

def kotlin_string(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


def android_string(s: str) -> str:
    """Escape text for an Android <string> resource."""
    out = s.replace("\\", "\\\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = out.replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")
    if out[:1] in "@?":
        out = "\\" + out
    return out


def properties_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", " ").replace("\r", " ")


# --------------------------------------------------------------------------- default icon (stdlib PNG)

def hex_to_rgb(color: str) -> Tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def make_png(size: int, bg: Tuple[int, int, int], fg: Tuple[int, int, int]) -> bytes:
    """A solid `bg` square with a centred `fg` disc. Pure stdlib, deterministic output."""
    cx = cy = (size - 1) / 2
    r2 = (size * 0.30) ** 2
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            c = fg if (x - cx) ** 2 + (y - cy) ** 2 <= r2 else bg
            row += bytes((c[0], c[1], c[2], 255))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )
