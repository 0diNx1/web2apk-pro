"""Keystore creation, APK alignment/signing/verification.

Passwords are never placed on command lines, in logs or in generated files: they are handed to
keytool/apksigner through environment variables of the child process only (`env:` / `-storepass:env`).
"""
from __future__ import annotations

import getpass
import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from .utils import AbortedError, SigningError

ENV_KEYSTORE = "WEB2APK_KEYSTORE"
ENV_ALIAS = "WEB2APK_KEY_ALIAS"
ENV_STORE_PASS = "WEB2APK_KEYSTORE_PASSWORD"
ENV_KEY_PASS = "WEB2APK_KEY_PASSWORD"
ENV_NEW_STORE_PASS = "WEB2APK_NEW_KEYSTORE_PASSWORD"

_CHILD_STORE = "W2A_CHILD_STORE_PW"
_CHILD_KEY = "W2A_CHILD_KEY_PW"


@dataclass
class SigningSpec:
    keystore: Path
    alias: str
    store_password: str = field(repr=False, default="")
    key_password: Optional[str] = field(repr=False, default=None)


def resolve_signing(keystore: Optional[str], alias: Optional[str], env: Mapping[str, str],
                    interactive: bool, require_passwords: bool = True,
                    prompt: Callable[[str], str] = None) -> SigningSpec:
    prompt = prompt or (lambda text: getpass.getpass(text, stream=__import__("sys").stderr))
    ks = keystore or env.get(ENV_KEYSTORE)
    if not ks:
        raise SigningError(
            "No keystore configured for the release build.",
            hint=("Create one:   web2apk keystore create ~/keys/release.p12 --alias mykey\n"
                  f"Then pass --keystore PATH (or set {ENV_KEYSTORE} / `web2apk config set keystore PATH`)."))
    ks_path = Path(ks).expanduser()
    if not ks_path.is_file():
        raise SigningError(f"Keystore not found: {ks_path}")
    alias = alias or env.get(ENV_ALIAS)
    if not alias:
        if not interactive:
            raise AbortedError(f"Key alias required (set {ENV_ALIAS} or --key-alias).")
        alias = input("Key alias: ").strip()
        if not alias:
            raise AbortedError("Key alias required.")
    store_pw = env.get(ENV_STORE_PASS, "")
    key_pw = env.get(ENV_KEY_PASS) or None
    if require_passwords:
        if not store_pw:
            if not interactive:
                raise AbortedError(f"Keystore password required: set {ENV_STORE_PASS} or run interactively.")
            store_pw = prompt(f"Keystore password for {ks_path.name}: ")
        if not store_pw:
            raise SigningError("Empty keystore password.")
    return SigningSpec(ks_path, alias, store_pw, key_pw)


def zipalign_command(zipalign: Path, src: Path, dst: Path) -> List[str]:
    return [str(zipalign), "-f", "-p", "4", str(src), str(dst)]


def apksigner_sign_command(apksigner: Path, spec: SigningSpec, src: Path, dst: Path) -> Tuple[List[str], Dict[str, str]]:
    """Return (argv, extra_env). The argv contains only env: references, never the secrets."""
    argv = [str(apksigner), "sign", "--ks", str(spec.keystore), "--ks-key-alias", spec.alias,
            "--ks-pass", f"env:{_CHILD_STORE}"]
    extra = {_CHILD_STORE: spec.store_password}
    if spec.key_password:
        argv += ["--key-pass", f"env:{_CHILD_KEY}"]
        extra[_CHILD_KEY] = spec.key_password
    argv += ["--out", str(dst), str(src)]
    return argv, extra


def apksigner_verify_command(apksigner: Path, apk: Path) -> List[str]:
    return [str(apksigner), "verify", "--verbose", "--print-certs", str(apk)]


def keytool_genkey_command(keytool: Path, keystore: Path, alias: str, dname: str,
                           validity_days: int = 10000, keysize: int = 4096) -> Tuple[List[str], str]:
    """PKCS12 keystore (single password). Password is read by keytool from an env var."""
    argv = [str(keytool), "-genkeypair", "-keystore", str(keystore), "-storetype", "PKCS12",
            "-alias", alias, "-keyalg", "RSA", "-keysize", str(keysize), "-validity", str(validity_days),
            "-dname", dname, f"-storepass:env", _CHILD_STORE]
    return argv, _CHILD_STORE


def create_keystore(keytool: Path, keystore: Path, alias: str, dname: str, password: str,
                    java_env: Optional[Mapping[str, str]] = None) -> None:
    keystore = keystore.expanduser()
    if keystore.exists():
        raise SigningError(f"Refusing to overwrite existing keystore: {keystore}",
                           hint="Choose a different path or move the old file yourself.")
    if len(password) < 6:
        raise SigningError("Keystore password must be at least 6 characters.")
    keystore.parent.mkdir(parents=True, exist_ok=True)
    argv, var = keytool_genkey_command(keytool, keystore, alias, dname)
    env = dict(java_env if java_env is not None else os.environ)
    env[var] = password
    try:
        p = subprocess.run(argv, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SigningError(f"keytool failed to start: {exc}") from exc
    if p.returncode != 0 or not keystore.is_file():
        raise SigningError("keytool failed:\n" + (p.stderr or p.stdout).strip())
    os.chmod(keystore, stat.S_IRUSR | stat.S_IWUSR)


def keystore_permission_warning(keystore: Path) -> Optional[str]:
    try:
        mode = keystore.stat().st_mode
    except OSError:
        return None
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        return f"{keystore} is readable by other users; run: chmod 600 {keystore}"
    return None
