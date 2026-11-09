from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)
from texture_courier import Texture

from texturefriend.cache.export import FORMATS, Format
from texturefriend.grid.cells import paint_texture, texture_brush
from texturefriend.grid.decodes import FULL_SIZE
from texturefriend.view.checkerboard import cycle_pane_tone
from texturefriend.view.formatting import format_count, format_size, format_time
from texturefriend.view.widgets import ClickTracker, bold, copyable, dim, height_for_width, wrapped

INSPECTOR_WIDTH = 260
INSPECTOR_MIN_WIDTH = 200

SIDEBAR_MIN_HEIGHT = 140

PANE_MARGIN = 16
PANE_SPACING = 6
HEADING_SPACING = 10

LABEL_SPACING = 12
ROW_SPACING = 4


def card_room(room: QSize) -> QSize:
    """The most a card in the pile is composed at, in device pixels

    The sidebar only ever scales the pile down, so a card larger than the room
    is work thrown away. The room is empty before the label is laid out, and
    as tall as a widget can be before the pane first shares out its height.
    """

    full = QSize(FULL_SIZE, FULL_SIZE)

    return full if room.isEmpty() else room.boundedTo(full)


class SidebarLabel(QLabel):
    clicked = Signal()
    dragged = Signal()
    menued = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._source = QPixmap()
        self._click = ClickTracker()

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # a label sizes itself to its pixmap by default, which would let a wide
        # texture pin the whole pane open at the width of the image. the height
        # comes from the image instead, so the name below sits the same distance
        # under a tall texture as it does under a wide one
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)

        self.setSizePolicy(policy)

    def set_source(self, pixmap: QPixmap, *, transparent: bool = False) -> None:
        self._source = pixmap

        self.sync_tip(transparent)
        self.updateGeometry()
        self.refit()

    def sync_tip(self, transparent: bool) -> None:
        actions = ["Drag out to save", "Right-click for options"]

        if transparent:
            actions.insert(0, "Click to cycle alpha mode")

        self.setToolTip("\n".join(actions))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # an empty sidebar has nothing to click on and nothing to drag out
        if self._click.press(event, taking=not self._source.isNull()):
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._click.pressed:
            super().mouseMoveEvent(event)
            return

        if self._click.dragged_past(event, QApplication.startDragDistance()):
            self.dragged.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._click.release(event, self.rect()):
            self.clicked.emit()
            return

        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self._click.cancel()

        event.accept()
        self.menued.emit(event.globalPos())

    def heightForWidth(self, width: int) -> int:
        if self._source.isNull():
            return SIDEBAR_MIN_HEIGHT

        fitted = self._source.size().scaled(self.box(width), Qt.AspectRatioMode.KeepAspectRatio)

        return max(fitted.height(), SIDEBAR_MIN_HEIGHT)

    def box(self, width: int) -> QSize:
        return QSize(width, self.maximumHeight()).boundedTo(self._source.size())

    def room(self) -> QSize:
        return QSize(self.width(), self.maximumHeight()) * self.devicePixelRatioF()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)

        self.refit()

    def refit(self) -> None:
        if self._source.isNull():
            self.clear()
            return

        ratio = self.devicePixelRatioF()

        # the pixmap is scaled in device pixels and then told what it was scaled
        # for, so the image is drawn at the density of the screen rather than at
        # the coarser one qt lays widgets out in. the box is the one the height
        # was asked for with, so the image lands at the size it was measured at
        fitted = self._source.scaled(
            self.box(self.width()) * ratio,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        rounded = QPixmap(fitted.size())
        rounded.setDevicePixelRatio(ratio)
        rounded.fill(Qt.GlobalColor.transparent)

        painter = QPainter(rounded)
        box = QRect(QPoint(), rounded.deviceIndependentSize().toSize())

        paint_texture(painter, texture_brush(fitted, ratio), box)

        painter.end()

        self.setPixmap(rounded)


def bezel_inset(button: QPushButton) -> int:
    option = QStyleOptionButton()
    button.initStyleOption(option)

    # the button has no size of its own yet, so it is measured at the one it asks for
    option.rect = QRect(QPoint(), button.sizeHint())

    bezel = button.style().subElementRect(QStyle.SubElement.SE_PushButtonLayoutItem, option, button)

    return max(option.rect.bottom() - bezel.bottom(), 0) if bezel.isValid() else 0


class InspectorPane(QWidget):
    dragged = Signal()
    menued = Signal(QPoint)
    exported = Signal(Format)

    # the sidebar has grown past the room its pile was composed for, which
    # it can only show by scaling up
    outgrown = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._texture: Texture | None = None
        self._composed = QSize()

        self.setMinimumWidth(INSPECTOR_MIN_WIDTH)

        self._empty = dim(QLabel("No selection"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._sidebar = SidebarLabel()
        # which checkerboard goes behind a texture in here and in the preview window,
        # the two places one is shown big enough to see through
        self._sidebar.clicked.connect(cycle_pane_tone)
        self._sidebar.dragged.connect(self.dragged)
        self._sidebar.menued.connect(self.menued)

        self._name = wrapped(copyable(bold(QLabel())))

        self._kind = wrapped(dim(QLabel()))

        self._info = QFormLayout()
        self._info.setContentsMargins(0, 0, 0, 0)
        self._info.setHorizontalSpacing(LABEL_SPACING)
        self._info.setVerticalSpacing(ROW_SPACING)
        self._info.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._info.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._info.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._info.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._dimensions = self.row("Dimensions")
        self._size = self.row("Size")
        self._date = self.row("Date")
        self._entry = self.row("Entry")

        information = QVBoxLayout()
        information.setContentsMargins(0, HEADING_SPACING, 0, 0)
        information.setSpacing(PANE_SPACING)
        information.addWidget(bold(QLabel("Information")))
        information.addLayout(self._info)

        self._information = height_for_width(QWidget())
        self._information.setLayout(information)

        details = QVBoxLayout()
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(PANE_SPACING)
        details.addWidget(self._name)
        details.addWidget(self._kind)
        details.addWidget(self._information)
        # the slack in the pane collects here, under the rows, rather than
        # between the sidebar and the name
        details.addStretch(1)
        details.addLayout(self.export_row())

        self._details = height_for_width(QWidget())
        self._details.setLayout(details)

        layout = QVBoxLayout(self)
        # the mac style draws a button's bezel inside a taller frame, and the
        # margin under it gives back the slack so the bezel sits on the margin.
        # styles that draw to the frame's edge report no slack
        layout.setContentsMargins(PANE_MARGIN, PANE_MARGIN, PANE_MARGIN, PANE_MARGIN - bezel_inset(self._export))
        layout.setSpacing(PANE_SPACING)
        # a hidden widget is skipped by the layout, stretch and all, so the two
        # states can share one column without stepping on each other
        layout.addWidget(self._empty, 1)
        layout.addWidget(self._sidebar)
        layout.addWidget(self._details, 1)
        self._layout = layout

        self.clear()

    def export_row(self) -> QHBoxLayout:
        menu = QMenu(self)

        for format in FORMATS:
            entry = menu.addAction(format.label)
            entry.triggered.connect(lambda _checked=False, format=format: self.exported.emit(format))

        # a push button with a menu is drawn by the mac style as a native
        # pull-down, arrow and all
        self._export = QPushButton("Export As")
        self._export.setMenu(menu)

        row = QHBoxLayout()
        row.setContentsMargins(0, HEADING_SPACING, 0, 0)
        row.addStretch(1)
        row.addWidget(self._export)

        return row

    def set_exportable(self, exportable: bool) -> None:
        self._export.setEnabled(exportable)

    def row(self, name: str) -> QLabel:
        value = wrapped(copyable(QLabel()))

        self._info.addRow(dim(QLabel(name)), value)

        return value

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)

        self.share_height()

    def share_height(self) -> None:
        margins = self._layout.contentsMargins()

        width = self.width() - margins.left() - margins.right()
        text = self._details.heightForWidth(width) if width > 0 else self._details.sizeHint().height()
        room = max(self.height() - margins.top() - margins.bottom() - PANE_SPACING - text, SIDEBAR_MIN_HEIGHT)

        if room != self._sidebar.maximumHeight():
            self._sidebar.setMaximumHeight(room)

        grown = card_room(self.sidebar_room())

        if self._composed.isValid() and (
            grown.width() > self._composed.width() or grown.height() > self._composed.height()
        ):
            self.outgrown.emit()

    @property
    def texture(self) -> Texture | None:
        return self._texture

    def clear(self) -> None:
        self._texture = None
        self._composed = QSize()

        self._sidebar.set_source(QPixmap())

        self._empty.setVisible(True)
        self._sidebar.setVisible(False)
        self._details.setVisible(False)

    def show_texture(self, texture: Texture, count: int, total: int) -> None:
        self._texture = texture

        self._empty.setVisible(False)
        self._sidebar.setVisible(True)
        self._details.setVisible(True)

        # finder titles a multiple selection by how much is in it rather than
        # by any one of the items, and a uuid up here would only name the one
        # texture the sidebar happened to land on
        self._name.setText(f"{format_count(count)} items" if count > 1 else texture.uuid)
        kind = "JPEG 2000 image" if texture.complete else "JPEG 2000 thumbnail"

        self._kind.setText(
            f"{format_count(count)} textures — {format_size(total)}"
            if count > 1
            else f"{kind} — {format_size(texture.image_size)}"
        )

        self._export.setToolTip(f"Export {format_count(count)} selected texture(s)")

        # the rows below are about the one texture, and a selection is titled
        # by its count above rather than described item by item
        self._information.setVisible(count == 1)

        self._size.setText(format_size(texture.image_size))
        self._date.setText(format_time(texture.time))
        self._entry.setText(format_count(texture.index))

        # the rows just changed height, and the sidebar is owed what is left
        self.share_height()

        # the caller follows with the image it has, which is not this one
        self.set_sidebar(QPixmap(), None)

    def sidebar_room(self) -> QSize:
        return self._sidebar.room()

    def pile_room(self) -> QSize:
        """The room a pile is composed for, which is watched for the pane outgrowing it"""

        self._composed = card_room(self.sidebar_room())

        return self._composed

    def sidebar_ratio(self) -> float:
        return self._sidebar.devicePixelRatioF()

    def set_sidebar(self, pixmap: QPixmap, natural: QSize | None, *, transparent: bool = False) -> None:
        self._sidebar.set_source(pixmap, transparent=transparent)

        self.share_height()

        if self._texture is not None and not self._texture.complete:
            # nothing decodes half a codestream, so the shape is never known and
            # the thumbnail beside it in the cache is what the sidebar is showing
            self._dimensions.setText("Unknown")
        elif natural is None:
            self._dimensions.setText("Decoding...")
        elif natural.isEmpty():
            self._dimensions.setText("Could not decode")
        else:
            self._dimensions.setText(f"{format_count(natural.width())} × {format_count(natural.height())}")
