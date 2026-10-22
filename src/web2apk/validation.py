"""Pure validators. Each returns None when valid or a human-readable error string."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Optional, Sequence

JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const",
    "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float",
    "for", "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native",
    "new", "package", "private", "protected", "public", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void",
    "volatile", "while", "true", "false", "null",
}
KOTLIN_KEYWORDS = {
    "as", "break", "class", "continue", "do", "else", "false", "for", "fun", "if", "in", "interface",
    "is", "null", "object", "package", "return", "super", "this", "throw", "true", "try",
    "typealias", "typeof", "val", "var", "when", "while",
}
RESERVED_SEGMENTS = JAVA_KEYWORDS | KOTLIN_KEYWORDS

PACKAGE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
VERSION_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

ORIENTATIONS = ("auto", "portrait", "landscape")
PERMISSIONS = ("camera", "microphone", "location")
MIN_SUPPORTED_SDK = 21


def check_package_id(value) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return "package_id must be a non-empty string"
    if len(value) > 255:
        return "package_id is too long (max 255 characters)"
    if not PACKAGE_RE.match(value):
        return (f"package_id '{value}' is invalid: use at least two lowercase dot-separated segments "
                "(e.g. com.example.app), each starting with a letter and containing only a-z, 0-9 and _")
    for seg in value.split("."):
        if seg in RESERVED_SEGMENTS:
            return f"package_id '{value}' contains the reserved word '{seg}'"
    return None


def check_app_name(value) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return "app_name must be a non-empty string"
    if len(value.strip()) > 50:
        return "app_name is too long (max 50 characters)"
    if CONTROL_RE.search(value):
        return "app_name must not contain control characters"
    return None


def check_version_name(value) -> Optional[str]:
    if not isinstance(value, str) or not VERSION_NAME_RE.match(value):
        return "version_name must be 1-32 characters of letters, digits, '.', '+', '_' or '-' (e.g. 1.0.0)"
    return None


def check_version_code(value) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 2_100_000_000):
        return "version_code must be an integer between 1 and 2100000000"
    return None


def check_color(value, field: str) -> Optional[str]:
    if not isinstance(value, str) or not COLOR_RE.match(value):
        return f"{field} must be a #RRGGBB hex color (got {value!r})"
    return None


def check_sdk_levels(min_sdk, target_sdk, compile_sdk: int) -> Optional[str]:
    for name, v in (("min_sdk", min_sdk), ("target_sdk", target_sdk)):
        if isinstance(v, bool) or not isinstance(v, int):
            return f"{name} must be an integer"
    if min_sdk < MIN_SUPPORTED_SDK:
        return f"min_sdk must be >= {MIN_SUPPORTED_SDK} (WebViewAssetLoader/androidx.webkit requirement)"
    if target_sdk < min_sdk:
        return f"target_sdk ({target_sdk}) must be >= min_sdk ({min_sdk})"
    if target_sdk > compile_sdk:
        return f"target_sdk ({target_sdk}) must be <= compile_sdk ({compile_sdk})"
    return None


def check_orientation(value) -> Optional[str]:
    if value not in ORIENTATIONS:
        return f"orientation must be one of {', '.join(ORIENTATIONS)} (got {value!r})"
    return None


def check_host(value) -> Optional[str]:
    if not isinstance(value, str) or not HOST_RE.match(value):
        return (f"allowed_origins entry {value!r} must be a lowercase hostname such as "
                "'example.com' or '*.example.com' (no scheme, port or path)")
    return None


def check_permissions(values) -> Optional[str]:
    bad = [v for v in values if v not in PERMISSIONS]
    if bad:
        return f"unknown permissions {bad}; allowed: {', '.join(PERMISSIONS)}"
    if len(set(values)) != len(values):
        return "permissions contains duplicates"
    return None


def check_relative_path(value, field: str, suffixes: Optional[Sequence[str]] = None,
                        allow_dot: bool = False) -> Optional[str]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return f"{field} must be a non-empty relative path"
    norm = value.replace("\\", "/")
    p = PurePosixPath(norm)
    if allow_dot and norm in (".", "./"):
        return None
    if p.is_absolute() or re.match(r"^[A-Za-z]:", norm) or ".." in p.parts:
        return f"{field} '{value}' must be relative and must not contain '..'"
    if suffixes and p.suffix.lower() not in suffixes:
        return f"{field} '{value}' must end with {' or '.join(suffixes)}"
    return None
