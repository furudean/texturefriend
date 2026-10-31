from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from texture_courier import Entry, Texture, TextureCache, Thumbnail

from texturefriend.cache import scan as scanning
from texturefriend.cache.color import BLIND_BASE_BYTES, FLAT_BASE_BYTES
from texturefriend.cache.scan import (
    PLACEHOLDER_BYTE,
    CacheScan,
    Scan,
    Traits,
    forget_gone,
    placeholder,
    stamp,
)

HEAD_BYTES = 600


def entry(image_size: int, uuid: str = "0" * 36, *, whole: bool = True) -> Texture:
    body_size = max(image_size - HEAD_BYTES, 0) if whole else 0

    return Texture(
        index=0,
        entry=Entry(uuid=uuid, image_size=image_size, body_size=body_size, time=datetime.now()),
        cache=cast("TextureCache", SimpleNamespace(cache_dir=Path("nowhere"))),
    )


def scan() -> CacheScan:
    return CacheScan([])


def kept(width: int = 16, height: int = 16, discard_level: int = 2, fill: int = 0) -> Thumbnail:
    return Thumbnail(
        width=width,
        height=height,
        components=4,
        discard_level=discard_level,
        pixels=bytes([fill]) * (width * height * 4),
    )


def reduced() -> Thumbnail:
    return kept(discard_level=6)


def filled(color: QColor) -> QImage:
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(color)

    return image


class TestDense:
    def test_a_texture_paying_for_a_picture_is_dense(self, app: QApplication) -> None:
        assert scan().dense(entry(FLAT_BASE_BYTES * 8), kept()) is True

    def test_a_texture_that_is_all_header_is_not(self, app: QApplication) -> None:
        assert scan().dense(entry(FLAT_BASE_BYTES // 2), kept()) is False

    def test_a_thumbnail_with_no_size_cannot_answer(self, app: QApplication) -> None:
        assert scan().dense(entry(FLAT_BASE_BYTES * 8), kept(width=0)) is False

    def test_a_large_texture_showing_nothing_still_answers_to_the_bytes(self, app: QApplication) -> None:
        assert scan().dense(entry(FLAT_BASE_BYTES * 32), reduced(), clear=True) is True

    def test_a_small_one_is_taken_at_its_word(self, app: QApplication) -> None:
        # nothing was lost reducing it, so the bytes are never asked
        assert scan().dense(entry(FLAT_BASE_BYTES * 32), kept(), clear=True) is False

    def test_a_thumbnail_of_one_pixel_leaves_it_to_the_bytes(self, app: QApplication) -> None:
        single = kept(width=1, height=1, discard_level=5)

        assert scan().dense(entry(BLIND_BASE_BYTES + 1), single) is True
        assert scan().dense(entry(BLIND_BASE_BYTES), single) is False

    def test_one_pixel_is_no_evidence_even_when_it_shows_nothing(self, app: QApplication) -> None:
        single = kept(width=1, height=1, discard_level=5)

        assert scan().dense(entry(BLIND_BASE_BYTES + 1), single, clear=True) is True


class TestSignature:
    def test_the_bytes_overrule_a_thumbnail_that_reports_one_colour(self, app: QApplication) -> None:
        found = scan().signature(entry(FLAT_BASE_BYTES * 8), kept(), filled(QColor("red")))

        assert found is not None
        assert found.flat is False

    def test_a_small_texture_keeps_the_one_colour_its_thumbnail_reports(self, app: QApplication) -> None:
        found = scan().signature(entry(FLAT_BASE_BYTES // 2), kept(), filled(QColor("red")))

        assert found is not None
        assert found.flat is True

    def test_a_texture_nobody_can_see_is_allowed_the_bytes_its_colors_cost(self, app: QApplication) -> None:
        # the colors are in the codestream and were paid for at full price, but
        # they are under an opacity plane that shows none of them, and it is too
        # small for the reduction to have hidden anything
        found = scan().signature(entry(FLAT_BASE_BYTES * 32), kept(), filled(QColor(0xFF, 0x00, 0x00, 0x00)))

        assert found is not None
        assert found.clear is True
        assert found.flat is True

    def test_a_texture_paying_far_past_that_is_a_sprite_sheet_the_thumbnail_lost(self, app: QApplication) -> None:
        found = scan().signature(entry(FLAT_BASE_BYTES * 32), reduced(), filled(QColor(0xFF, 0x00, 0x00, 0x00)))

        assert found is not None
        assert found.flat is False
        assert found.clear is False

    def test_an_incomplete_texture_is_never_simple(self, app: QApplication) -> None:
        found = scan().signature(entry(FLAT_BASE_BYTES * 2, whole=False), kept(), filled(QColor("red")))

        assert found is not None
        assert found.flat is False
        assert found.clear is False

    def test_a_thumbnail_that_cannot_be_read_has_no_signature(self, app: QApplication) -> None:
        assert scan().signature(entry(FLAT_BASE_BYTES * 8), kept(), QImage()) is None


class TestPlaceholder:
    def test_a_slot_of_nothing_but_the_fill_is_a_placeholder(self) -> None:
        assert placeholder(kept(fill=PLACEHOLDER_BYTE)) is True

    def test_a_thumbnail_with_a_picture_in_it_is_not(self) -> None:
        grey = kept(fill=PLACEHOLDER_BYTE)
        marked = Thumbnail(
            width=grey.width,
            height=grey.height,
            components=grey.components,
            discard_level=grey.discard_level,
            pixels=grey.pixels[:-1] + b"\x81",
        )

        assert placeholder(marked) is False

    def test_a_slot_of_another_fill_is_not(self) -> None:
        # black and white thumbnails are written by the viewer and mean it
        assert placeholder(kept(fill=0x00)) is False
        assert placeholder(kept(fill=0xFF)) is False

    def test_a_thumbnail_with_no_pixels_is_not(self) -> None:
        assert placeholder(kept(width=0)) is False


class TestForgetGone:
    def test_only_textures_still_held_as_they_were_keep_their_traits(self) -> None:
        held = entry(10, "a" * 36)
        rewritten = entry(20, "b" * 36)
        evicted = entry(30, "c" * 36)

        known = {stamp(texture): Traits(None, None) for texture in (held, rewritten, evicted)}

        forget_gone(known, [held, entry(21, "b" * 36)])

        assert list(known) == [stamp(held)]


def unread_scan(count: int) -> tuple[CacheScan, list[Scan]]:
    textures = [entry(10, f"{row:036}") for row in range(count)]

    found = CacheScan(textures)
    reports: list[Scan] = []
    found.done.connect(reports.append)

    return found, reports


class TestSlices:
    @pytest.fixture(autouse=True)
    def one_texture_a_slice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scanning, "SLICE_SECONDS", 0)
        monkeypatch.setattr(CacheScan, "traits", lambda _self, _texture: None)

    def test_a_scan_reports_once_however_often_it_is_resumed(self, app: QApplication) -> None:
        # every scroll that settles resumes the scan, finished or not
        found, reports = unread_scan(3)

        found.start()

        for _ in range(10):
            app.processEvents()
            found.resume()

        assert len(reports) == 1

    def test_a_cancelled_scan_never_reports(self, app: QApplication) -> None:
        found, reports = unread_scan(3)

        found.start()
        found.cancel()
        found.resume()

        for _ in range(10):
            app.processEvents()

        assert reports == []
