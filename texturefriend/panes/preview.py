from math import ceil

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QHideEvent,
    QKeyEvent,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QWidget
from texture_courier import Texture

from texturefriend.view.checkerboard import (
    cycle_pane_tone,
    pane_checkerboard,
    pixmap_lightness,
    set_picked_lightness,
)
from texturefriend.view.formatting import format_count, format_size
from texturefriend.view.widgets import ClickTracker
from texturefriend.view.zoomable import ZoomableView

WINDOW_TITLE = "Preview"
WINDOW_SIZE = 480
MIN_PANE_SIZE = 32
MIN_OPEN_SIZE = 400
MAX_OPEN_SHARE = 2 / 3

MIP_FLOOR = 64


def nearest(edge: int, length: int, low: int, high: int) -> int:
    return min(max(edge, low), max(high - length + 1, low))


def preview_title(texture: Texture, natural: QSize, scale: float | None = None) -> str:
    dimensions = f"{format_count(natural.width())} × {format_count(natural.height())}"

    # the shape of a texture is not known until it has been decoded
    about = ", ".join(
        part
        for part in (
            dimensions if not natural.isEmpty() else "",
            format_size(texture.image_size),
            "" if texture.complete else "incomplete",
        )
        if part
    )

    # how large the texture is drawn against its own pixels
    zoom = f" @ {format_count(round(scale * 100))}%" if scale is not None else ""

    return f"{texture.uuid}{zoom} ({about})"


def open_size(natural: QSize, room: QSize | None) -> QSize:
    size = natural * ceil(MIN_OPEN_SIZE / max(natural.width(), natural.height()))

    # a texture bigger than the screen is shrunk to fit it, keeping its shape
    if room is not None and (size.width() > room.width() or size.height() > room.height()):
        size = size.scaled(room, Qt.AspectRatioMode.KeepAspectRatio)

    return size


def drawn_sharp(pixmap: QSize, natural: QSize, drawn: QSize) -> bool:
    return pixmap == natural and drawn.width() >= natural.width() and drawn.height() >= natural.height()


def mip_levels(pixmap: QPixmap) -> list[QPixmap]:
    """The pixmap and each halving of it, down to the floor

    Smooth pixmap drawing is bilinear, which shimmers once it shrinks a texture
    more than twice over. Drawing from the nearest halving keeps it within that.
    """

    levels = [pixmap]

    while max(levels[-1].width(), levels[-1].height()) // 2 >= MIP_FLOOR:
        level = levels[-1]
        half = QSize(max(1, level.width() // 2), max(1, level.height() // 2))

        levels.append(
            level.scaled(half, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    return levels


def mip_level(levels: list[QPixmap], pixels: QSize) -> QPixmap:
    """The smallest level that still covers what it is drawn into"""

    return next(
        (level for level in reversed(levels) if level.width() >= pixels.width() and level.height() >= pixels.height()),
        levels[0],
    )


class PreviewWindow(ZoomableView):
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool)

        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.setMinimumSize(MIN_PANE_SIZE, MIN_PANE_SIZE)

        # what the window opens at with no geometry of its own to restore
        self.resize(WINDOW_SIZE, WINDOW_SIZE)

        self._pixmap = QPixmap()
        self._levels = [self._pixmap]
        self._message = ""
        self._lightness: float | None = None
        self._click = ClickTracker()

        # the texture shown, so a decode landing over its own stand-in keeps
        # the zoom and a new texture starts back at fit
        self._texture: Texture | None = None
        self._natural = QSize()

        # the texture the window has been shaped to, which is what keeps a
        # decode landing on top of the stand-in for the same texture from
        # shaping the window a second time
        self._shaped_for: str | None = None

        # the size the window was last given by hand, which later textures
        # are fitted inside until the window is hidden
        self._box: QSize | None = None
        self._shaped_size = QSize()

        # the left edge and the vertical middle of where the window was last
        # put by hand, which every shape is kept at
        self._anchor: QPoint | None = None
        self._shaping = False

        self.setWindowTitle(WINDOW_TITLE)

    def present(self) -> None:
        self.show()
        self.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)

        self.closed.emit()

    def clear(self) -> None:
        self._shaped_for = None
        self._texture = None
        self._natural = QSize()

        self.reset_zoom()
        self.set_image(QPixmap(), "No selection")

    def show_texture(
        self,
        texture: Texture,
        decoded: tuple[QPixmap, QSize] | None,
        standing: tuple[QPixmap, QSize] | None,
    ) -> None:
        pixmap, natural = (decoded or standing) or (QPixmap(), QSize())

        known = self._texture is not None and self._texture.uuid == texture.uuid

        self._texture = texture
        self._natural = natural

        if not known:
            self.reset_zoom()

        self.sync_title()

        if not texture.complete:
            message = "Texture incomplete"
        else:
            message = "Could not decode" if decoded is not None else "Decoding..."

        self.set_image(pixmap, message)

        # a texture is shown in the shape it was drawn in, which is not known
        # until a decode has landed, and is taken once, leaving whatever the
        # window is put at by hand afterwards to stand
        if not natural.isEmpty() and texture.uuid != self._shaped_for:
            self._shaped_for = texture.uuid

            self.shape_window(natural)

    def set_image(self, pixmap: QPixmap, message: str) -> None:
        self._pixmap = pixmap
        self._levels = mip_levels(pixmap)
        self._message = message
        self._lightness = pixmap_lightness(pixmap)

        set_picked_lightness(self._lightness)

        self.update()

    def can_zoom(self) -> bool:
        return not self._pixmap.isNull()

    def zoom_changed(self) -> None:
        self.sync_title()

    def sync_title(self) -> None:
        if self._texture is None:
            self.setWindowTitle(WINDOW_TITLE)
            return

        scale = self.width() * self.zoom / self._natural.width() if not self._natural.isEmpty() else None

        self.setWindowTitle(preview_title(self._texture, self._natural, scale))

    def room(self) -> QRect | None:
        screen = self.screen()

        return screen.availableGeometry() if screen is not None else None

    def shape_window(self, natural: QSize) -> None:
        # the title bar and the border take room the pane never gets
        chrome = self.frameGeometry().size() - self.size()
        room = self.room()

        if self._box is not None:
            box = self._box if room is None else self._box.boundedTo(room.size() - chrome)
            size = natural.scaled(box, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            size = open_size(natural, None if room is None else room.size() * MAX_OPEN_SHARE - chrome)

        self._shaping = True
        self._shaped_size = size

        try:
            self.resize(size)
            self.anchor()
        finally:
            self._shaping = False

    def anchor(self) -> None:
        frame = self.frameGeometry()
        anchor = self._anchor if self._anchor is not None else QPoint(frame.left(), frame.center().y())

        left = anchor.x()
        top = anchor.y() - frame.height() // 2

        room = self.room()

        # a shape too big to be held at the anchor is kept on the screen
        # instead, which is worth more than the anchor
        if room is not None:
            left = nearest(left, frame.width(), room.left(), room.right())
            top = nearest(top, frame.height(), room.top(), room.bottom())

        self.move(left, top)

    def remember_anchor(self) -> None:
        if self._shaping:
            return

        frame = self.frameGeometry()
        self._anchor = QPoint(frame.left(), frame.center().y())

    def remember_box(self) -> None:
        # a resize that lands at the shaped size is the window system catching
        # up with the shape, and one while hidden is a restore or a show
        if self._shaping or not self.isVisible() or self.size() == self._shaped_size:
            return

        self._box = self.size()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)

        self.remember_box()
        self.remember_anchor()
        self.sync_title()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)

        # macOS hides tool windows while the app is in the background, which
        # is no reason to forget the size
        if not event.spontaneous():
            self._box = None

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)

        self.remember_anchor()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)

        if self._pixmap.isNull():
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        else:
            # the texture is given the whole window, which is kept in the shape
            # the texture was drawn in so that filling it holds that shape
            target = self.view()

            checkerboard = pane_checkerboard(self._lightness) if self._pixmap.hasAlphaChannel() else None

            if checkerboard is not None:
                brush = QBrush(checkerboard)
                brush.setTransform(self.zoom_transform())

                painter.fillRect(target, brush)

            # a zoom draws the part of the texture in view, clear of the scroll bars
            drawn = self.size() * self.devicePixelRatioF() * self.zoom
            level = mip_level(self._levels, drawn)
            source = self.zoomed_source(level.size())

            if not drawn_sharp(self._pixmap.size(), self._natural, drawn):
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            painter.drawPixmap(QRectF(target), level, source)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)

        # an empty window has no texture to cycle the checkerboard behind
        if self._click.press(event, taking=not self._pixmap.isNull()):
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)

        # a pan is not a click
        if self.panning:
            self._click.cancel()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)

        if self._click.release(event, self.rect()):
            event.accept()
            cycle_pane_tone()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.close()
            return

        super().keyPressEvent(event)
