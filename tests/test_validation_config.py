import json

import pytest

from web2apk import validation as v
from web2apk.config import (GlobalConfig, ProjectConfig, apply_dict, load_project_file, resolve_project_config,
                            save_project_file)
from web2apk.utils import ConfigError


@pytest.mark.parametrize("pid", ["com.example.app", "org.my_app.v2", "a.b"])
def test_package_ids_valid(pid):
    assert v.check_package_id(pid) is None


@pytest.mark.parametrize("pid", ["", "app", "Com.Example.App", "com..app", "com.1app.x", "com.example.class",
                                 "com.example.app.", "com.exa mple.app", "com.example.new", "com.exa-mple.app", None, 5])
def test_package_ids_invalid(pid):
    assert v.check_package_id(pid)


def test_other_validators():
    assert v.check_version_name("1.0.0-beta+1") is None
    assert v.check_version_name("../x")
    assert v.check_version_code(1) is None
    assert v.check_version_code(0) and v.check_version_code(True) and v.check_version_code("1")
    assert v.check_color("#aabbcc", "c") is None and v.check_color("red", "c")
    assert v.check_sdk_levels(23, 35, 35) is None
    assert v.check_sdk_levels(19, 35, 35) and v.check_sdk_levels(30, 29, 35) and v.check_sdk_levels(23, 36, 35)
    assert v.check_host("example.com") is None and v.check_host("*.example.com") is None
    assert v.check_host("https://example.com") and v.check_host("localhost") and v.check_host("a.com/x")
    assert v.check_permissions(["camera"]) is None and v.check_permissions(["root"])
    assert v.check_relative_path("a/b.html", "e", (".html",)) is None
    assert v.check_relative_path("../b.html", "e") and v.check_relative_path("/b.html", "e")
    assert v.check_app_name("My App") is None and v.check_app_name("  ") and v.check_app_name("a\x00b")


def test_config_defaults_validate():
    ProjectConfig().validate()


def test_config_rejects_unknown_key_and_types():
    with pytest.raises(ConfigError) as e:
        apply_dict(ProjectConfig(), {"app_nam": "x", "version_code": "1", "fullscreen": 1}, "t")
    msg = str(e.value)
    assert "did you mean 'app_name'" in msg and "version_code" in msg and "fullscreen" in msg


def test_config_cross_field_rules():
    cfg = ProjectConfig(internet_access=False, allow_downloads=True, allow_cleartext=True, allowed_origins=["a.com"])
    with pytest.raises(ConfigError) as e:
        cfg.validate()
    assert str(e.value).count("requires internet_access") == 3


def test_config_collects_all_errors():
    with pytest.raises(ConfigError) as e:
        ProjectConfig(package_id="bad", status_bar_color="x", min_sdk=10).validate()
    assert str(e.value).count("\n  - ") == 3


def test_layers_and_explicit_keys(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    g = GlobalConfig.load()
    g.set("defaults.orientation", "portrait")
    g.set("defaults.min_sdk", "24")
    save_project_file(proj, {"app_name": "From File", "min_sdk": 26})
    cfg, explicit = resolve_project_config(proj, g, {"app_name": None, "version_code": 7})
    assert (cfg.app_name, cfg.min_sdk, cfg.orientation, cfg.version_code) == ("From File", 26, "portrait", 7)
    assert {"app_name", "min_sdk", "orientation", "version_code"} <= explicit
    assert "package_id" not in explicit


def test_bad_project_json(tmp_path):
    (tmp_path / "web2apk.json").write_text("{nope")
    with pytest.raises(ConfigError):
        load_project_file(tmp_path)


def test_global_config_roundtrip_and_validation():
    g = GlobalConfig.load()
    g.set("sdk_path", "/opt/android-sdk")
    g.set("compile_sdk", "35")
    g.save()
    g2 = GlobalConfig.load()
    assert g2.get("sdk_path") == "/opt/android-sdk" and g2.compile_sdk == 35
    with pytest.raises(ConfigError):
        g2.set("nonsense", "1")
    with pytest.raises(ConfigError):
        g2.set("gradle_mode", "fast")
    with pytest.raises(ConfigError):
        g2.set("defaults.nope", "1")
    assert g2.unset("sdk_path") and not g2.unset("sdk_path")
    assert json.loads(g2.path.read_text()).get("compile_sdk") == 35
