"""The bigger decodes the panes ask for

The grid decodes every texture down to a cell. The inspector and the preview
each want one texture at a time and at a size of their own, so each keeps its
own small store rather than competing with the wall of cells for room in the
pixmap cache.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from texture_courier import Texture

from texturefriend.view.images import placeholder

# the largest size a cell is drawn at
FULL_SIZE = 800

# how many inspector-sized decodes are kept. a selection walked with the arrow
# keys comes back through the ones just left, and a screenful of them is more
# than anyone walks back through
FULL_CACHE = 12


def full_size(natural: QSize) -> QSize:
    return natural.scaled(QSize(FULL_SIZE, FULL_SIZE).boundedTo(natural), Qt.AspectRatioMode.KeepAspectRatio)


def held_to(size: QSize, room: QSize) -> QSize:
    """Scaled down into the room, and left alone if it already fits"""

    if size.width() > room.width() or size.height() > room.height():
        return size.scaled(room, Qt.AspectRatioMode.KeepAspectRatio)

    return size


class FullDecodes:
    """Inspector-sized decodes, and the few most recent of them kept

    Held rather than put in the pixmap cache: these are much larger than a
    cell, and a handful of them would push out the thousands of cells the
    grid is painting from.

    Each is decoded for the room the pile had when it was asked for, which
    is often a halving or two short of the texture. The room goes in beside
    it, so a pane widened past that room knows to ask again.
    """

    def __init__(self) -> None:
        self._ready: dict[str, tuple[QPixmap, QSize, QSize | None]] = {}
        self._running: dict[str, QSize | None] = {}

    def ready(self, uuid: str) -> tuple[QPixmap, QSize] | None:
        ready = self._ready.get(uuid)

        return (ready[0], ready[1]) if ready is not None else None

    def fills(self, uuid: str, room: QSize | None) -> bool:
        """Whether the decode in hand is as sharp as a card in the room can show"""

        if (ready := self._ready.get(uuid)) is None:
            return False

        pixmap, natural, decoded_for = ready

        # one that failed or came in at full size has nothing finer to give
        if natural.isEmpty() or decoded_for is None or pixmap.size() == full_size(natural):
            return True

        if room is None:
            return False

        # compared as cards rather than rooms, since a room only grown along the
        # side the texture does not reach lays the same card
        wanted = held_to(full_size(natural), room)
        had = held_to(full_size(natural), decoded_for)

        return wanted.width() <= had.width() and wanted.height() <= had.height()

    def wanted(self, texture: Texture, room: QSize | None) -> bool:
        if not texture.complete or texture.uuid in self._running:
            return False

        self._running[texture.uuid] = room

        return True

    def landed(self, uuid: str, image: QImage, natural: QSize) -> bool:
        """Take a decode, and say whether it is one the inspector still wants

        Always, as it happens: unlike the preview, the inspector shows a pile
        rather than one texture, and a decode that lands after the selection
        has moved on is still one of the cards on some earlier pile. The bool
        is here so the two stores read the same way at their call sites.
        """

        room = self._running.pop(uuid, None)

        # a texture that cannot be decoded caches its placeholder, or every
        # reselect would set the same doomed decode going again
        pixmap = placeholder() if image.isNull() else QPixmap.fromImage(image)

        # put back at the end, so a decode redone for a wider pane is not the
        # first one dropped
        self._ready.pop(uuid, None)
        self._ready[uuid] = (pixmap, natural, room)

        while len(self._ready) > FULL_CACHE:
            del self._ready[next(iter(self._ready))]

        return True

    def clear(self) -> None:
        self._ready.clear()
        self._running.clear()


class PreviewDecodes:
    """The one texture the preview window is on

    A preview shows one texture at whatever size it was drawn, which is far
    too large to keep several of: the one being looked at is the only one
    worth the room.
    """

    def __init__(self) -> None:
        self._ready: tuple[str, QPixmap, QSize] | None = None
        self._showing: str | None = None
        self._running: set[str] = set()

    def now_showing(self, texture: Texture) -> tuple[QPixmap, QSize] | None:
        """Saying which texture the preview is on is what makes a decode the
        selection has walked past droppable when it lands.
        """

        self._showing = texture.uuid

        if self._ready is not None and self._ready[0] == texture.uuid:
            return self._ready[1], self._ready[2]

        return None

    def wanted(self, texture: Texture) -> bool:
        if not texture.complete or texture.uuid in self._running:
            return False

        self._running.add(texture.uuid)

        return True

    def landed(self, uuid: str, image: QImage, natural: QSize) -> bool:
        self._running.discard(uuid)

        if uuid != self._showing:
            return False

        # a texture that could not be decoded comes back null, and is held that
        # way so a reselect does not set the same doomed decode going again
        self._ready = (uuid, QPixmap.fromImage(image), natural)

        return True

    def clear(self) -> None:
        self._ready = None
        self._showing = None
        self._running.clear()
