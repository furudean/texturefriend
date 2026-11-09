from types import SimpleNamespace
from typing import cast

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from texture_courier import Texture

from texturefriend.grid.decodes import FullDecodes, full_size

UUID = "0" * 36


def texture() -> Texture:
    return cast("Texture", SimpleNamespace(uuid=UUID, complete=True))


def landed(natural: QSize, decoded: QSize, room: QSize | None) -> FullDecodes:
    fulls = FullDecodes()

    assert fulls.wanted(texture(), room)

    fulls.landed(UUID, QImage(decoded, QImage.Format.Format_RGBA8888), natural)

    return fulls


class TestFills:
    def test_nothing_in_hand_fills_nothing(self, app: QApplication) -> None:
        assert not FullDecodes().fills(UUID, QSize(456, 800))

    def test_a_reduced_decode_fills_the_room_it_was_made_for(self, app: QApplication) -> None:
        fulls = landed(QSize(1024, 1024), QSize(512, 512), QSize(456, 800))

        assert fulls.fills(UUID, QSize(456, 800))
        assert fulls.fills(UUID, QSize(300, 800))

    def test_a_reduced_decode_falls_short_of_a_wider_room(self, app: QApplication) -> None:
        fulls = landed(QSize(1024, 1024), QSize(512, 512), QSize(456, 800))

        assert not fulls.fills(UUID, QSize(600, 800))
        assert not fulls.fills(UUID, None)

    def test_a_room_grown_where_the_card_does_not_reach_still_fills(self, app: QApplication) -> None:
        fulls = landed(QSize(1024, 1024), QSize(512, 512), QSize(456, 600))

        assert fulls.fills(UUID, QSize(456, 800))

    def test_a_decode_at_full_size_fills_any_room(self, app: QApplication) -> None:
        natural = QSize(512, 256)
        fulls = landed(natural, full_size(natural), QSize(456, 800))

        assert fulls.fills(UUID, QSize(800, 800))
        assert fulls.fills(UUID, None)

    def test_a_failed_decode_is_not_asked_for_again(self, app: QApplication) -> None:
        fulls = FullDecodes()
        fulls.wanted(texture(), QSize(200, 200))
        fulls.landed(UUID, QImage(), QSize())

        assert fulls.fills(UUID, QSize(800, 800))

    def test_one_running_is_not_asked_for_twice(self, app: QApplication) -> None:
        fulls = FullDecodes()

        assert fulls.wanted(texture(), QSize(200, 200))
        assert not fulls.wanted(texture(), QSize(800, 800))
