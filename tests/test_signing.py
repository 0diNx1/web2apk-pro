import os
import shutil
import stat
from pathlib import Path

import pytest

from conftest import make_exe
from web2apk import signing
from web2apk.utils import AbortedError, SigningError


def _ks(tmp_path):
    p = tmp_path / "k.p12"
    p.write_bytes(b"x")
    p.chmod(0o600)
    return p


def test_resolve_from_env(tmp_path):
    ks = _ks(tmp_path)
    env = {signing.ENV_KEYSTORE: str(ks), signing.ENV_ALIAS: "key", signing.ENV_STORE_PASS: "s3cret!"}
    spec = signing.resolve_signing(None, None, env, interactive=False)
    assert spec.keystore == ks and spec.alias == "key" and spec.store_password == "s3cret!" and spec.key_password is None
    assert "s3cret" not in repr(spec)                 # secrets never appear in reprs/logs


def test_missing_keystore_explains_how_to_create():
    with pytest.raises(SigningError) as e:
        signing.resolve_signing(None, None, {}, interactive=False)
    assert "keystore create" in (e.value.hint or "")
    with pytest.raises(SigningError):
        signing.resolve_signing("/no/such.p12", "a", {}, interactive=False)


def test_noninteractive_requires_password_and_alias(tmp_path):
    ks = _ks(tmp_path)
    with pytest.raises(AbortedError):
        signing.resolve_signing(str(ks), "a", {}, interactive=False)
    with pytest.raises(AbortedError):
        signing.resolve_signing(str(ks), None, {signing.ENV_STORE_PASS: "x"}, interactive=False)


def test_interactive_prompts_without_echo_helper(tmp_path):
    ks = _ks(tmp_path)
    asked = []
    spec = signing.resolve_signing(str(ks), "a", {}, interactive=True, prompt=lambda q: asked.append(q) or "pw")
    assert spec.store_password == "pw" and len(asked) == 1


def test_sign_command_never_contains_password(tmp_path):
    spec = signing.SigningSpec(tmp_path / "k.p12", "key", "topsecret", "keysecret")
    argv, extra = signing.apksigner_sign_command(Path("/sdk/apksigner"), spec, Path("in.apk"), Path("out.apk"))
    joined = " ".join(argv)
    assert "topsecret" not in joined and "keysecret" not in joined
    assert "env:W2A_CHILD_STORE_PW" in joined and "env:W2A_CHILD_KEY_PW" in joined
    assert extra["W2A_CHILD_STORE_PW"] == "topsecret"
    assert signing.zipalign_command(Path("/z"), Path("a"), Path("b"))[:3] == ["/z", "-f", "-p"]
    assert signing.apksigner_verify_command(Path("/s"), Path("a.apk"))[1] == "verify"


def test_keytool_command_uses_env_for_password(tmp_path):
    argv, var = signing.keytool_genkey_command(Path("/jdk/bin/keytool"), tmp_path / "k.p12", "key", "CN=x")
    assert "-storepass:env" in argv and var in argv and "PKCS12" in argv


def test_create_keystore_refuses_overwrite(tmp_path):
    ks = _ks(tmp_path)
    with pytest.raises(SigningError):
        signing.create_keystore(Path("/bin/true"), ks, "a", "CN=x", "password1")
    assert ks.read_bytes() == b"x"
    with pytest.raises(SigningError):
        signing.create_keystore(Path("/bin/true"), tmp_path / "new.p12", "a", "CN=x", "123")   # too short


@pytest.mark.skipif(not shutil.which("keytool"), reason="needs a JDK keytool")
def test_create_keystore_real_keytool(tmp_path):
    target = tmp_path / "keys" / "r.p12"
    signing.create_keystore(Path(shutil.which("keytool")), target, "mykey", "CN=Test", "password1")
    assert target.is_file() and stat.S_IMODE(target.stat().st_mode) == 0o600


def test_permission_warning(tmp_path):
    ks = _ks(tmp_path)
    assert signing.keystore_permission_warning(ks) is None
    ks.chmod(0o644)
    assert "chmod 600" in signing.keystore_permission_warning(ks)
