import struct
import zlib

import pytest

from web2apk.utils import (PathSafetyError, android_string, human_size, is_within, kotlin_string, make_png,
                           resolve_within, slugify, write_if_changed)


def test_resolve_within_ok(tmp_path):
    (tmp_path / "a").mkdir()
    assert resolve_within(tmp_path, "a/b.html") == (tmp_path / "a" / "b.html").resolve()
    assert resolve_within(tmp_path, ".") == tmp_path.resolve()


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/etc/passwd", "..\\x", "C:/win", "", "a\x00b"])
def test_resolve_within_rejects_traversal(tmp_path, bad):
    with pytest.raises(PathSafetyError):
        resolve_within(tmp_path, bad)


def test_resolve_within_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "link").symlink_to(outside)
    with pytest.raises(PathSafetyError):
        resolve_within(base, "link/file.txt")


def test_is_within(tmp_path):
    assert is_within(tmp_path / "a", tmp_path)
    assert not is_within(tmp_path.parent, tmp_path)


def test_escaping():
    assert kotlin_string('a"b$c\\') == '"a\\"b\\$c\\\\"'
    assert android_string("Tom's & <App>") == "Tom\\'s &amp; &lt;App&gt;"
    assert android_string("@home").startswith("\\@")


def test_slugify_and_size():
    assert slugify("My Cool App!") == "my-cool-app"
    assert slugify("!!!") == "app"
    assert human_size(2048) == "2.0 KB"


def test_png_is_valid():
    png = make_png(8, (1, 2, 3), (255, 255, 255))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    pos, idat = 8, b""
    while pos < len(png):
        ln, tag = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + ln]
        crc = struct.unpack(">I", png[pos + 8 + ln:pos + 12 + ln])[0]
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF
        if tag == b"IDAT":
            idat += body
        pos += 12 + ln
    assert len(zlib.decompress(idat)) == 8 * (1 + 8 * 4)


def test_write_if_changed(tmp_path):
    p = tmp_path / "x" / "f.txt"
    assert write_if_changed(p, "a") is True
    assert write_if_changed(p, "a") is False
    assert write_if_changed(p, "b") is True
