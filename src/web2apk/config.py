"""Project configuration (web2apk.json) and global configuration (~/.config/web2apk/config.json)."""
from __future__ import annotations

import difflib
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import validation as v
from .utils import ConfigError

PROJECT_CONFIG_NAME = "web2apk.json"
DEFAULT_GRADLE_VERSION = "8.14.3"
DEFAULT_AGP_VERSION = "8.7.3"
DEFAULT_KOTLIN_VERSION = "2.0.21"
DEFAULT_COMPILE_SDK = 35


@dataclass
class ProjectConfig:
    app_name: str = "Web App"
    package_id: str = "com.example.webapp"
    version_name: str = "1.0.0"
    version_code: int = 1
    entry_point: str = "auto"          # "auto" or a path relative to the web root
    web_root: str = "auto"             # "auto", ".", or a sub-directory such as "public"
    min_sdk: int = 23
    target_sdk: int = 35
    orientation: str = "auto"          # auto | portrait | landscape
    internet_access: bool = False      # False = offline-only app (no INTERNET permission)
    allow_cleartext: bool = False      # plain http:// (requires internet_access)
    allowed_origins: List[str] = field(default_factory=list)  # hosts allowed to load inside the WebView
    fullscreen: bool = False
    allow_file_uploads: bool = True
    allow_downloads: bool = False      # requires internet_access
    permissions: List[str] = field(default_factory=list)      # camera | microphone | location
    js_bridge: bool = False
    debug_webview: bool = False        # only honoured in debuggable (debug) builds
    status_bar_color: str = "#1F2937"
    splash_color: str = "#111827"
    icon: Optional[str] = None         # path to a PNG/JPEG/WebP, relative to the project dir

    def validate(self, compile_sdk: int = DEFAULT_COMPILE_SDK) -> None:
        errs: List[Optional[str]] = [
            v.check_app_name(self.app_name),
            v.check_package_id(self.package_id),
            v.check_version_name(self.version_name),
            v.check_version_code(self.version_code),
            v.check_sdk_levels(self.min_sdk, self.target_sdk, compile_sdk),
            v.check_orientation(self.orientation),
            v.check_color(self.status_bar_color, "status_bar_color"),
            v.check_color(self.splash_color, "splash_color"),
            v.check_permissions(self.permissions),
        ]
        if self.entry_point != "auto":
            errs.append(v.check_relative_path(self.entry_point, "entry_point", (".html", ".htm")))
        if self.web_root != "auto":
            errs.append(v.check_relative_path(self.web_root, "web_root", allow_dot=True))
        if self.icon is not None:
            errs.append(v.check_relative_path(self.icon, "icon") if not os.path.isabs(self.icon) else None)
        errs.extend(v.check_host(h) for h in self.allowed_origins)
        if not self.internet_access:
            for flag, name in ((self.allow_cleartext, "allow_cleartext"), (self.allow_downloads, "allow_downloads"),
                               (bool(self.allowed_origins), "allowed_origins")):
                if flag:
                    errs.append(f"{name} requires internet_access=true")
        problems = [e for e in errs if e]
        if problems:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(problems),
                              hint=f"Fix the values in {PROJECT_CONFIG_NAME} or the matching command-line options.")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_KINDS = {
    "app_name": "str", "package_id": "str", "version_name": "str", "version_code": "int",
    "entry_point": "str", "web_root": "str", "min_sdk": "int", "target_sdk": "int",
    "orientation": "str", "internet_access": "bool", "allow_cleartext": "bool",
    "allowed_origins": "list", "fullscreen": "bool", "allow_file_uploads": "bool",
    "allow_downloads": "bool", "permissions": "list", "js_bridge": "bool", "debug_webview": "bool",
    "status_bar_color": "str", "splash_color": "str", "icon": "optstr",
}
assert set(_KINDS) == {f.name for f in fields(ProjectConfig)}


def _kind_ok(kind: str, value: Any) -> bool:
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "list":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if kind == "optstr":
        return value is None or isinstance(value, str)
    return False


def apply_dict(cfg: ProjectConfig, data: Dict[str, Any], source: str) -> ProjectConfig:
    """Return a copy of `cfg` updated from `data`; unknown keys and wrong types are errors."""
    errs = []
    new = ProjectConfig(**{f.name: getattr(cfg, f.name) for f in fields(ProjectConfig)})
    for key, value in data.items():
        if key not in _KINDS:
            close = difflib.get_close_matches(key, _KINDS, n=1)
            errs.append(f"unknown key '{key}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
        elif not _kind_ok(_KINDS[key], value):
            errs.append(f"'{key}' must be of type {_KINDS[key]} (got {type(value).__name__})")
        else:
            setattr(new, key, list(value) if isinstance(value, list) else value)
    if errs:
        raise ConfigError(f"Invalid {source}:\n  - " + "\n  - ".join(errs))
    return new


def load_project_file(project_dir: Path) -> Dict[str, Any]:
    path = project_dir / PROJECT_CONFIG_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def save_project_file(project_dir: Path, updates: Dict[str, Any]) -> Path:
    path = project_dir / PROJECT_CONFIG_NAME
    data = load_project_file(project_dir)
    data.update(updates)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- global config

GLOBAL_KEYS = {
    "sdk_path": str, "java_home": str, "gradle_path": str, "output_dir": str, "keystore": str,
    "key_alias": str, "compile_sdk": int, "agp_version": str, "kotlin_version": str,
    "gradle_version": str, "gradle_mode": str, "defaults": dict,
}


def config_dir(env=None) -> Path:
    env = os.environ if env is None else env
    if env.get("WEB2APK_CONFIG_DIR"):
        return Path(env["WEB2APK_CONFIG_DIR"])
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "web2apk"


def cache_dir(env=None) -> Path:
    env = os.environ if env is None else env
    if env.get("WEB2APK_CACHE_DIR"):
        return Path(env["WEB2APK_CACHE_DIR"])
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "web2apk"


class GlobalConfig:
    def __init__(self, path: Optional[Path] = None, data: Optional[Dict[str, Any]] = None):
        self.path = path or (config_dir() / "config.json")
        self.data: Dict[str, Any] = data or {}

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GlobalConfig":
        cfg = cls(path)
        if cfg.path.is_file():
            try:
                data = json.loads(cfg.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ConfigError(f"Cannot read {cfg.path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError(f"{cfg.path} must contain a JSON object")
            unknown = [k for k in data if k not in GLOBAL_KEYS]
            if unknown:
                raise ConfigError(f"Unknown keys in {cfg.path}: {', '.join(unknown)}")
            cfg.data = data
        return cfg

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def compile_sdk(self) -> int:
        return int(self.data.get("compile_sdk", DEFAULT_COMPILE_SDK))

    @property
    def agp_version(self) -> str:
        return self.data.get("agp_version", DEFAULT_AGP_VERSION)

    @property
    def kotlin_version(self) -> str:
        return self.data.get("kotlin_version", DEFAULT_KOTLIN_VERSION)

    @property
    def gradle_version(self) -> str:
        return self.data.get("gradle_version", DEFAULT_GRADLE_VERSION)

    @property
    def gradle_mode(self) -> str:
        return self.data.get("gradle_mode", "auto")

    def set(self, key: str, raw: str) -> None:
        try:
            value: Any = json.loads(raw)
        except ValueError:
            value = raw
        if key.startswith("defaults."):
            name = key.split(".", 1)[1]
            if name not in _KINDS:
                raise ConfigError(f"Unknown project default '{name}'. Known: {', '.join(_KINDS)}")
            if not _kind_ok(_KINDS[name], value):
                raise ConfigError(f"defaults.{name} must be of type {_KINDS[name]}")
            self.data.setdefault("defaults", {})[name] = value
            return
        if key not in GLOBAL_KEYS or key == "defaults":
            raise ConfigError(f"Unknown config key '{key}'. Known: {', '.join(k for k in GLOBAL_KEYS if k != 'defaults')}, defaults.<name>")
        expected = GLOBAL_KEYS[key]
        if expected is int and not (isinstance(value, int) and not isinstance(value, bool)):
            raise ConfigError(f"{key} must be an integer")
        if expected is str:
            value = raw if not isinstance(value, str) else value
        if key == "gradle_mode" and value not in ("auto", "system", "wrapper"):
            raise ConfigError("gradle_mode must be auto, system or wrapper")
        self.data[key] = value

    def unset(self, key: str) -> bool:
        if key.startswith("defaults."):
            return self.data.get("defaults", {}).pop(key.split(".", 1)[1], None) is not None
        return self.data.pop(key, None) is not None


def resolve_project_config(project_dir: Path, gcfg: GlobalConfig,
                           overrides: Dict[str, Any]) -> Tuple[ProjectConfig, Set[str]]:
    """Layers: built-in defaults < global defaults < project file < command line.

    Returns the (unvalidated) config and the set of keys that were set explicitly by any layer.
    """
    cfg = ProjectConfig()
    explicit: Set[str] = set()
    gd = gcfg.get("defaults", {})
    if gd:
        cfg = apply_dict(cfg, gd, f"defaults in {gcfg.path}")
        explicit |= set(gd)
    pf = load_project_file(project_dir)
    cfg = apply_dict(cfg, pf, str(project_dir / PROJECT_CONFIG_NAME))
    explicit |= set(pf)
    ov = {k: val for k, val in overrides.items() if val is not None}
    cfg = apply_dict(cfg, ov, "command-line options")
    explicit |= set(ov)
    return cfg, explicit
