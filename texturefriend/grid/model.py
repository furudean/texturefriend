import logging
import threading
from collections.abc import Callable, Iterable

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRunnable,
    QSize,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QImage, QPixmap, QPixmapCache
from texture_courier import Texture, TextureCache, TextureCacheError

from texturefriend.cache.decode import declared_size
from texturefriend.cache.likeness import describe
from texturefriend.cache.scan import CacheScan, KnownTraits, Scan
from texturefriend.grid.cellcache import insert_cell, nearest_cell, remove_cells
from texturefriend.grid.decodes import FULL_SIZE, FullDecodes, PreviewDecodes
from texturefriend.grid.narrowing import Narrowing
from texturefriend.grid.queue import DecodeQueue
from texturefriend.view.cellsize import cell_size
from texturefriend.view.checkerboard import checkerboard_generation
from texturefriend.view.formatting import format_size, format_time
from texturefriend.view.images import (
    THUMBNAIL_SIZE,
    decode_image,
    fit_image,
    placeholder,
    thumbnail_image,
)

log = logging.getLogger(__name__)

FULL_PRIORITY = 1
PREVIEW_PRIORITY = 2

KB_PER_MB = 1024

# a decoded cell is 100x100 at 32 bits, or 39 KB, so this holds about 27k
# textures. a retina screen doubles each side, and the largest zoom nearly
# triples it again, which leaves room for about 800
PIXMAP_CACHE_MB = 1024
PIXMAP_CACHE_KB = PIXMAP_CACHE_MB * KB_PER_MB

# an invalid index is the root of a list model, and it is a plain value type,
# so one shared instance stands in for the default argument
ROOT = QModelIndex()

type Index = QModelIndex | QPersistentModelIndex

# whether the row's entry is one the cache never finished downloading, which the
# grid marks and nothing else has a way of asking about
INCOMPLETE_ROLE = Qt.ItemDataRole.UserRole

# whether the row's texture holds one solid color or nothing visible at all,
# which the grid rings when it is not leaving those out altogether
SIMPLE_ROLE = Qt.ItemDataRole.UserRole + 1


def sidebar_key(uuid: str) -> str:
    return f"sidebar:{uuid}"


def alpha_key(uuid: str) -> str:
    return f"alpha:{uuid}"


def cell_pixels(ratio: float) -> int:
    # rounded the way the slot is, so a settled cell is drawn without resampling
    return round(round(cell_size()) * ratio)


class DecodeSignals(QObject):
    done = Signal(str, QImage, QSize)


class DecodeTask(QRunnable):
    def __init__(
        self,
        texture: Texture,
        reads: threading.Lock,
        signals: DecodeSignals,
        size: int | None = THUMBNAIL_SIZE,
        *,
        upscale: bool = True,
        checkerboard: bool = True,
        ratio: float = 1.0,
        threads: Callable[[], int] | None = None,
        fit: QSize | None = None,
    ):
        super().__init__()

        self._texture = texture
        self._reads = reads
        self._signals = signals
        self._size = size
        self._upscale = upscale
        self._board = checkerboard
        self._ratio = ratio
        self._threads = threads
        self._fit = fit

    @Slot()
    def run(self) -> None:
        image, natural = self.decode()

        self._signals.done.emit(self._texture.uuid, image, natural)

    def decode(self) -> tuple[QImage, QSize]:
        try:
            # buffered with lock
            with self._reads:
                codestream = self._texture.codestream()

            # counted as the decode starts, since the pool may have filled or
            # drained since the task was queued
            threads = self._threads() if self._threads else 1

            image = decode_image(codestream, threads, fit=self._fit)
        except (TextureCacheError, OSError) as e:
            # a cache is full of entries the viewer never finished writing, so
            # one that will not decode is ordinary rather than news. the cell
            # falls back to its thumbnail either way
            log.debug("could not decode %s: %s", self._texture.uuid, e)

            return QImage(), QSize()

        fitted = fit_image(image, self._size, upscale=self._upscale, checkerboard=self._board, ratio=self._ratio)

        # a reduced decode comes back smaller than the texture, so its size is
        # taken from the header
        declared = declared_size(codestream)
        natural = QSize(*declared) if declared is not None else image.size()

        return fitted, natural


class TextureModel(QAbstractListModel):
    full_ready = Signal(str)
    preview_ready = Signal(str)
    ranked = Signal()
    thinned = Signal()

    def __init__(
        self,
        textures: list[Texture],
        cache: TextureCache,
        known: KnownTraits,
        ratio: float = 1.0,
        parent: QObject | None = None,
        *,
        incomplete_hidden: bool = False,
    ):
        super().__init__(parent)

        self._textures = list(textures)
        self._lookup = {texture.uuid: texture for texture in self._textures}
        # what is being asked of the grid, and what the scan found to answer
        # it with
        self._narrowing = Narrowing(
            incomplete=frozenset(row for row, texture in enumerate(self._textures) if not texture.whole()),
            incomplete_hidden=incomplete_hidden,
        )
        self._filtered_textures = [self._textures[row] for row in self._narrowing.candidates(len(self._textures))]
        self._filtered_rows = {texture.uuid: row for row, texture in enumerate(self._filtered_textures)}
        # the textures the scan found no picture in, which are left out of the
        # grid or ringed in it depending on what the menu says
        self._simple: set[str] = set()
        self._no_sidebar: set[str] = set()
        self._natural: dict[str, QSize] = {}
        self._fulls = FullDecodes()
        # a preview window shows the one texture it is on, so the last one
        # asked for is the only one worth the room a full sized decode takes
        self._previews = PreviewDecodes()
        self._generation = checkerboard_generation()
        # cells are decoded at the screen's resolution, so on a retina screen a
        # 100 point cell is 200 pixels across
        self._ratio = ratio
        self._pixels = cell_pixels(ratio)

        self._decodes = DecodeQueue(self, start=self.start_decode, pixels=self._pixels)

        self._prefetched_at: int | None = None

        self._signals = DecodeSignals(self)
        self._signals.done.connect(self.decoded)

        # the inspector's decodes report separately, since they come back at
        # their own size and land in their own cache
        self._full_signals = DecodeSignals(self)
        self._full_signals.done.connect(self.full_decoded)

        # the preview window decodes at whatever size the texture is and keeps
        # its alpha, since it lays the checkerboard down behind the image rather than
        # into it
        self._preview_signals = DecodeSignals(self)
        self._preview_signals.done.connect(self.preview_decoded)

        self._scan = CacheScan(self._textures, known, self)
        self._scan.done.connect(self.scanned)

        if not incomplete_hidden:
            self._scan.read_incomplete()

        self._scan.start()

    @property
    def reads(self) -> threading.Lock:
        """The turn a texture's bytes have to be read on

        Every texture in a cache reads through one shared BytesIO, so anything
        reading alongside the grid's decodes has to wait for the same lock.
        """

        return self._decodes.reads

    def rowCount(self, parent: Index = ROOT) -> int:
        return 0 if parent.isValid() else len(self._filtered_textures)

    def total(self) -> int:
        return self._narrowing.total(len(self._textures))

    @property
    def narrowed(self) -> bool:
        return self._narrowing.narrowed

    @property
    def scanning(self) -> bool:
        return self._narrowing.waiting

    @property
    def matching(self) -> bool:
        """Whether the grid is ranked against a picture rather than filtered"""

        return self._narrowing.matching

    @property
    def colors(self) -> list[QColor]:
        return list(self._narrowing.colors)

    def hidden(self) -> int:
        return self._narrowing.hidden()

    def texture(self, row: int) -> Texture:
        return self._filtered_textures[row]

    def row(self, uuid: str) -> int | None:
        return self._filtered_rows.get(uuid)

    def flags(self, index: Index) -> Qt.ItemFlag:
        flags = super().flags(index)

        if not index.isValid():
            return flags

        return flags | Qt.ItemFlag.ItemIsDragEnabled

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> QPixmap | str | bool | None:
        if not index.isValid():
            return None

        texture = self._filtered_textures[index.row()]

        if role == Qt.ItemDataRole.DecorationRole:
            return self.decoration(texture)

        if role == INCOMPLETE_ROLE:
            return not texture.whole()

        if role == SIMPLE_ROLE:
            return texture.uuid in self._simple

        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [texture.uuid, format_time(texture.time)]

            if not texture.whole():
                lines.append(f"Incomplete — {format_size(texture.cached_size)} of {format_size(texture.image_size)}")

            return "\n".join(lines)

        return None

    def decoration(self, texture: Texture) -> QPixmap:
        decoded = nearest_cell(texture.uuid, self._pixels)

        if not decoded.isNull():
            # a cell at another size stands in for its replacement until that
            # lands
            if not self._decodes.fits(decoded):
                self.request(texture)

            return decoded

        self.request(texture)

        return self.sidebar(texture)

    def sidebar(self, texture: Texture) -> QPixmap:
        """The thumbnail beside a texture in the cache, at the size and on the checkerboard of a cell"""

        if texture.uuid in self._no_sidebar:
            return placeholder()

        cached = nearest_cell(sidebar_key(texture.uuid), self._pixels)

        if not cached.isNull() and self._decodes.fits(cached):
            return cached

        image = self.thumbnail(texture, self._pixels, ratio=self._ratio)

        if image.isNull():
            self._no_sidebar.add(texture.uuid)

            return placeholder()

        pixmap = QPixmap.fromImage(image)

        insert_cell(sidebar_key(texture.uuid), self._pixels, pixmap)

        return pixmap

    def thumbnail(
        self,
        texture: Texture,
        size: int = THUMBNAIL_SIZE,
        *,
        checkerboard: bool = True,
        ratio: float = 1.0,
    ) -> QImage:
        try:
            kept = texture.thumbnail
        except (TextureCacheError, OSError) as e:
            # not every entry has a thumbnail beside it, and the placeholder
            # stands in for the ones that do not
            log.debug("no thumbnail for %s: %s", texture.uuid, e)

            kept = None

        if kept is None:
            return QImage()

        return thumbnail_image(kept, size, checkerboard=checkerboard, ratio=ratio)

    def cell(self, texture: Texture) -> QPixmap:
        """Whatever the grid already holds for a texture, without decoding"""

        cell = nearest_cell(texture.uuid, self._pixels)

        return cell if not cell.isNull() else nearest_cell(sidebar_key(texture.uuid), self._pixels)

    def full_decode(
        self,
        texture: Texture,
        room: QSize | None = None,
        *,
        decode: bool = True,
    ) -> tuple[QPixmap, QSize] | None:
        """The decode in hand, or nothing until one is in

        A decode is started when there is none or the one in hand is too coarse
        for a card in the room, unless asked only to look. Without a room, the
        decode is for a card at full size.
        """

        ready = self._fulls.ready(texture.uuid)

        if decode and not self._fulls.fills(texture.uuid, room) and self._fulls.wanted(texture, room):
            # the selection is what the user is looking at, so this goes in
            # ahead of the screenful of cells the grid has already asked for
            task = DecodeTask(
                texture,
                self.reads,
                self._full_signals,
                FULL_SIZE,
                upscale=False,
                checkerboard=False,
                threads=self._decodes.spare_threads,
                fit=room,
            )

            self._decodes.pool.start(task, FULL_PRIORITY)

        return ready

    def natural(self, texture: Texture) -> QSize:
        return self._natural.get(texture.uuid, QSize())

    def stand_in(self, texture: Texture) -> tuple[QPixmap, QSize] | None:
        """The best decode already in hand, with the size it came in at if known

        Nothing is started for it: this is only what a pane or a window can
        put up on the spot while the decode it really wants is out. The shape
        is handed over beside it, since a decode at any size knew what it was.

        What comes back keeps its opacity wherever it can, since a stand-in is
        drawn larger than it was kept and a checkerboard painted into it is drawn
        larger with it, at squares several times the size of the ones the
        decode it stands in for is laid over.
        """

        uuid = texture.uuid

        if (ready := self.full_decode(texture, decode=False)) is not None:
            return ready

        kept = QPixmap()

        if QPixmapCache.find(alpha_key(uuid), kept):
            return kept, self.natural(texture)

        # a texture with no thumbnail beside it in the cache falls back to the
        # grid's cell, which has the checkerboard painted into it at the size a cell is
        if uuid in self._no_sidebar:
            return self.cell_standing(texture)

        image = self.thumbnail(texture, checkerboard=False)

        if image.isNull():
            self._no_sidebar.add(uuid)

            return self.cell_standing(texture)

        pixmap = QPixmap.fromImage(image)

        QPixmapCache.insert(alpha_key(uuid), pixmap)

        return pixmap, self.natural(texture)

    def cell_standing(self, texture: Texture) -> tuple[QPixmap, QSize] | None:
        cell = self.cell(texture)

        return (cell, self.natural(texture)) if not cell.isNull() else None

    def preview(self, texture: Texture) -> tuple[QPixmap, QSize] | None:
        if (ready := self._previews.now_showing(texture)) is not None:
            return ready

        if self._previews.wanted(texture):
            task = DecodeTask(
                texture,
                self.reads,
                self._preview_signals,
                None,
                checkerboard=False,
                threads=self._decodes.spare_threads,
            )

            self._decodes.pool.start(task, PREVIEW_PRIORITY)

        return None

    def restyle(self) -> bool:
        """Take note of a checkerboard that has since moved

        The cells are the grid's, and are dropped by whoever calls this; what is
        here is the cell decodes still out, which are the only ones with a checkerboard
        painted into what they bring back. Returns whether the checkerboard has moved.
        """

        generation = checkerboard_generation()

        if generation == self._generation:
            return False

        self._generation = generation

        # a cell decode already running was handed the old checkerboard, so whatever
        # it comes back with is painted on a background that is no longer there
        self._decodes.restyle()

        return True

    def resize_cells(self, ratio: float) -> bool:
        pixels = cell_pixels(ratio)

        if pixels == self._pixels and ratio == self._ratio:
            return False

        self._ratio = ratio
        self._pixels = pixels

        self._decodes.resize(pixels)

        return True

    def set_filters(self, colors: list[QColor]) -> bool:
        self._narrowing.colors = list(colors)

        return self.apply_filters()

    def set_reference(self, image: QImage | None) -> bool:
        """Rank the grid against a picture, or against nothing when there is none"""

        self._narrowing.reference = describe(image) if image is not None else None

        return self.apply_filters()

    def set_simple_hidden(self, hidden: bool) -> bool:
        self._narrowing.simple_hidden = hidden

        return self.apply_filters()

    def set_incomplete_hidden(self, hidden: bool) -> bool:
        self._narrowing.incomplete_hidden = hidden

        if not hidden:
            self._scan.read_incomplete()

        return self.apply_filters()

    def apply_filters(self) -> bool:
        kept = self._narrowing.shown_rows(len(self._textures))

        if kept is None:
            return False

        self.reorder([self._textures[row] for row in kept])

        return True

    def reorder(self, textures: list[Texture]) -> None:
        if textures == self._filtered_textures:
            return

        self.beginResetModel()

        self._filtered_textures = textures
        self._filtered_rows = {texture.uuid: row for row, texture in enumerate(textures)}

        self._decodes.clear()
        self._prefetched_at = None

        self.endResetModel()

    @Slot(object)
    def scanned(self, scan: Scan) -> None:
        first = self._narrowing.scan is None

        self._narrowing.scan = scan

        if first:
            self._simple = self._narrowing.flat_uuids([texture.uuid for texture in self._textures])
        elif self._narrowing.incomplete_hidden:
            return

        if self._narrowing.asking and self.apply_filters():
            if self._narrowing.narrowed:
                self.ranked.emit()
            else:
                self.thinned.emit()
        elif first and self._simple and self._filtered_textures:
            # nothing is being narrowed, so no reset goes out to redraw the
            # grid, and the rows the scan just found no picture in would sit
            # there unringed until something else happened to touch them
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._filtered_textures) - 1, 0),
                [SIMPLE_ROLE],
            )

    def hold(self, *, decodes: bool) -> None:
        self._scan.pause()

        if decodes:
            self._decodes.hold()

    def release(self) -> None:
        self._scan.resume()
        self._decodes.release()

    def start_decode(self, texture: Texture, priority: int) -> None:
        task = DecodeTask(
            texture,
            self.reads,
            self._signals,
            self._pixels,
            ratio=self._ratio,
            fit=QSize(self._pixels, self._pixels),
        )

        self._decodes.pool.start(task, priority)

    def request(self, texture: Texture) -> None:
        self._decodes.request(texture)

    def wanted(self, texture: Texture) -> bool:
        return self._decodes.wanted(texture)

    def prefetched_near(self, offset: int, slack: int) -> bool:
        return self._prefetched_at is not None and abs(offset - self._prefetched_at) < slack

    def prefetch(self, rows: Iterable[int], visible: tuple[int, int], offset: int) -> None:
        first, last = visible
        on_screen = {self.texture(row).uuid for row in range(first, last + 1)}

        self._prefetched_at = offset

        self._decodes.refill(map(self.texture, rows), on_screen)

    def learn(self, uuid: str, natural: QSize) -> None:
        if not natural.isEmpty():
            self._natural[uuid] = natural

    @Slot(str, QImage, QSize)
    def decoded(self, uuid: str, image: QImage, natural: QSize) -> None:
        # a cell is cut down to the size of a cell, but the decode behind it saw
        # the texture whole, and that is worth keeping for the panes that ask
        self.learn(uuid, natural)

        if self._decodes.landed(uuid, decoded=not image.isNull()):
            insert_cell(uuid, self._pixels, QPixmap.fromImage(image))

            # the real texture is in now, so the sidebar is dead weight
            remove_cells(sidebar_key(uuid))

        # started after the arrival is booked in, so the slot freed by this
        # decode is one the next of them can be handed
        self._decodes.pump()

        row = self._filtered_rows.get(uuid)

        if row is None:
            return

        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    @Slot(str, QImage, QSize)
    def full_decoded(self, uuid: str, image: QImage, natural: QSize) -> None:
        self.learn(uuid, natural)

        if self._fulls.landed(uuid, image, natural):
            self.full_ready.emit(uuid)

    @Slot(str, QImage, QSize)
    def preview_decoded(self, uuid: str, image: QImage, natural: QSize) -> None:
        # the shape stands even if the selection has moved on from the image
        self.learn(uuid, natural)

        if self._previews.landed(uuid, image, natural):
            self.preview_ready.emit(uuid)

    def shutdown(self) -> None:
        # a decode the pool already started still reports back, and this model
        # is on its way out, so a stale image could land on top of a fresher
        # one under the same key. dropping the connection drops those reports
        self._signals.done.disconnect(self.decoded)
        self._full_signals.done.disconnect(self.full_decoded)
        self._preview_signals.done.disconnect(self.preview_decoded)

        self._scan.cancel()

        self._fulls.clear()
        self._previews.clear()

        self._decodes.shutdown()
