"""What the grid decodes next

The grid asks for far more textures than it can decode at once, and asks for
them again every time it scrolls. Which of them are worth starting, which are
already in hand, and which order they go to the pool in is bookkeeping of its
own, kept here rather than among the model's rows.
"""

import threading
from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, QThreadPool
from PySide6.QtGui import QPixmap
from texture_courier import Texture

from texturefriend.cache.decode import POOL_THREADS
from texturefriend.grid.cellcache import has_cell

# the priority a cell goes to the pool at. a row on screen outranks one in the
# band either side of it, which is decoded only once the screen is filled
CELL_PRIORITY = 0
AHEAD_PRIORITY = -1

# how many decodes are allowed out at once. more than the threads, so a thread
# that finishes has its next one already waiting rather than idling while the
# event loop gets round to handing one over
DECODES_IN_FLIGHT = POOL_THREADS * 2


class DecodeQueue:
    """The textures waiting to be decoded into cells, and the ones already out

    `start` is what actually puts a texture on the pool, which is the model's
    to say: the queue knows what should be decoded and in what order, and not
    how a decode is made.
    """

    def __init__(self, parent: QObject, start: Callable[[Texture, int], None], pixels: int) -> None:
        self._start = start

        # how many pixels across a cell is decoded at
        self._pixels = pixels

        # drained from the end, so whatever fills it puts the rows wanted
        # soonest last. each entry carries the priority it goes to the pool at,
        # since the pool takes work back off nobody, and the texture itself, so
        # that draining the queue needs nothing looked up again
        self._queue: dict[str, tuple[int, Texture]] = {}
        self._running: set[str] = set()
        self._failed: set[str] = set()

        # cell decodes that set out under a checkerboard that has since moved,
        # which is to say the ones whose results are already out of date on
        # arrival
        self._stale: set[str] = set()

        # held while the grid is being resized. every decode started then is out
        # of date before it lands, and its threads take the GIL from the layout
        self._held = False

        self._pool = QThreadPool(parent)
        self._pool.setMaxThreadCount(POOL_THREADS)

        self._reads = threading.Lock()

    @property
    def reads(self) -> threading.Lock:
        """The turn a texture's bytes have to be read on

        Every texture in a cache reads through one shared BytesIO, so anything
        reading alongside the grid's decodes has to wait for the same lock.
        """

        return self._reads

    @property
    def pool(self) -> QThreadPool:
        return self._pool

    def spare_threads(self) -> int:
        """The pool threads not in use, counting the one that asks"""

        return max(1, self._pool.maxThreadCount() - self._pool.activeThreadCount() + 1)

    def wanted(self, texture: Texture) -> bool:
        uuid = texture.uuid

        # an entry the cache never finished downloading has no codestream to
        # decode, so the thumbnail beside it in the cache is all there ever is
        if not texture.complete:
            return False

        if uuid in self._running or uuid in self._failed:
            return False

        return not has_cell(uuid, self._pixels)

    def fits(self, cell: QPixmap) -> bool:
        return max(cell.width(), cell.height()) == self._pixels

    def resize(self, pixels: int) -> None:
        self._pixels = pixels

        self.restyle()

    def enqueue(self, texture: Texture, priority: int) -> bool:
        """Put a texture in the queue, and say whether the queue moved

        A texture already waiting is only moved for a more urgent ask, and is
        moved to the end when it is: the queue drains backwards.
        """

        uuid = texture.uuid

        if (waiting := self._queue.get(uuid)) is not None:
            if priority <= waiting[0]:
                return False

            del self._queue[uuid]
        elif not self.wanted(texture):
            return False

        self._queue[uuid] = (priority, texture)

        return True

    def request(self, texture: Texture) -> None:
        if self.enqueue(texture, CELL_PRIORITY):
            self.pump()

    def refill(self, textures: Iterable[Texture], on_screen: set[str]) -> None:
        """Replace the queue with the band around the viewport

        Whatever was waiting was asked for at a place the grid has since
        scrolled away from, so it is dropped rather than added to.
        """

        self._queue = {
            texture.uuid: (CELL_PRIORITY if texture.uuid in on_screen else AHEAD_PRIORITY, texture)
            for texture in textures
            if self.wanted(texture)
        }

        self.pump()

    def hold(self) -> None:
        self._held = True

    def release(self) -> None:
        self._held = False

        self.pump()

    def pump(self) -> None:
        if self._held:
            return

        while self._queue and len(self._running) < DECODES_IN_FLIGHT:
            uuid, (priority, texture) = self._queue.popitem()

            self._running.add(uuid)

            self._start(texture, priority)

    def landed(self, uuid: str, *, decoded: bool) -> bool:
        """Take a decode off the running list, and say whether to keep it

        A decode that set out under a checkerboard the app has since moved off
        is thrown away: what it painted is a background that is no longer
        there, and the repaint that follows asks for the texture again.
        """

        self._running.discard(uuid)

        if uuid in self._stale:
            self._stale.discard(uuid)

            return False

        if not decoded:
            # nothing goes in the pixmap cache for a texture that would not
            # decode, so without this the next repaint asks for it forever
            self._failed.add(uuid)

            return False

        return True

    def restyle(self) -> None:
        self._stale = set(self._running)

    def clear(self) -> None:
        self._queue.clear()

    def shutdown(self) -> None:
        self._queue.clear()

        self._pool.clear()
        self._pool.waitForDone()
