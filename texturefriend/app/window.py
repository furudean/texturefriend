from pathlib import Path
from threading import Lock
from typing import ClassVar

from PySide6.QtCore import (
    QDir,
    QEvent,
    QItemSelectionModel,
    QPoint,
    QSettings,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QImage,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QListView,
    QMainWindow,
    QWidget,
)
from texture_courier import Texture, TextureCache, TextureCacheError

from texturefriend import APP_DISPLAY_NAME
from texturefriend.app.about import AboutDialog
from texturefriend.app.actions import AppMenu, WindowActions
from texturefriend.app.alerts import warn
from texturefriend.app.drag import DRAG_LIMIT, file_data, staged
from texturefriend.app.exporting import ExportRun, ask_for_directory
from texturefriend.app.session import AppState
from texturefriend.cache.decode import POOL_THREADS
from texturefriend.cache.export import Format
from texturefriend.cache.recents import RecentCaches
from texturefriend.cache.scan import KnownTraits, forget_gone, stamp
from texturefriend.cache.suggested import paths as suggested_paths
from texturefriend.grid.cards import grid_cards, stack_textures
from texturefriend.grid.cellcache import remove_cells
from texturefriend.grid.cells import CELL_PADDING, CellDelegate, TextureGrid
from texturefriend.grid.model import TextureModel, sidebar_key
from texturefriend.grid.prefetch import prefetch
from texturefriend.grid.summary import empty_message, narrowed_summary, ranked_summary
from texturefriend.grid.summary import grid_summary as summary_of
from texturefriend.panes.dropzone import DropZone
from texturefriend.panes.filters import FilterBar
from texturefriend.panes.inspector import INSPECTOR_WIDTH, InspectorPane
from texturefriend.panes.preview import PreviewWindow
from texturefriend.panes.sidebar import paint as paint_pane
from texturefriend.panes.status import WindowStatus
from texturefriend.panes.zoom import ZoomControl
from texturefriend.settings import (
    FILTERS_KEY,
    GEOMETRY_KEY,
    SPLITTER_KEY,
    stored_blob,
)
from texturefriend.view.cellsize import CellSizeChanges
from texturefriend.view.checkerboard import (
    CheckerboardChanges,
    reset_pane_tone,
    set_picked_lightness,
    sync_checkerboard,
)
from texturefriend.view.formatting import format_count
from texturefriend.view.images import decode_image, image_file, image_filter, readable_image
from texturefriend.view.splitter import HairlineSplitter
from texturefriend.view.stack import stack_pixmap

NEW_WINDOW_OFFSET = QPoint(32, 32)

ZOOM_SETTLE_MS = 150
SCROLL_SETTLE_MS = 250

SCANNING_MESSAGE = "Identifying texture characteristics..."


class MainWindow(QMainWindow):
    # the windows the app has open, the preview they share and the about box
    # all belong to the app rather than to any one window. every window reaches
    # the same one, and the app builds it on the way up
    _app: ClassVar[AppState] = AppState()

    def __init__(self) -> None:
        super().__init__()

        MainWindow._app.session.add(self)

        settings = QSettings()

        self.setWindowTitle(APP_DISPLAY_NAME)

        # a directory dropped anywhere on the window is opened in it, and a
        # picture dropped on it is searched for
        self.setAcceptDrops(True)

        # default window size
        self.resize(800, 600)

        self.restoreGeometry(stored_blob(settings, GEOMETRY_KEY))

        self._cache: TextureCache | None = None

        self._known: KnownTraits = {}

        # the model the grid is on. the view hands back a QAbstractItemModel,
        # which every caller would otherwise have to narrow again
        self._model: TextureModel | None = None
        self._stack: list[Texture] = []
        self._job: ExportRun | None = None
        self._staging = False

        self._summary = ""

        # the texture the panes are showing, which is what says whether a click on
        # one of them is still about what is in front of the user
        self._standing = ""

        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(150)  # wait for layout to settle
        self._settle.timeout.connect(self.settle_action)

        # the model holds its background work while the grid moves, and picks
        # it up again once the grid has been still for a moment
        self._moving = QTimer(self)
        self._moving.setSingleShot(True)
        self._moving.timeout.connect(self.settled_action)

        self._view = TextureGrid()
        self._view.setViewMode(QListView.ViewMode.IconMode)
        self._view.setSpacing(CELL_PADDING // 2)
        self._view.setItemDelegate(CellDelegate(self._view))
        self._view.setResizeMode(QListView.ResizeMode.Adjust)
        self._view.setMovement(QListView.Movement.Static)
        # a texture is dragged out of the window as a file, and nothing is
        # ever dropped into the grid
        self._view.setDragDropMode(QListView.DragDropMode.DragOnly)
        self._view.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._view.setUniformItemSizes(True)
        self._view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._view.setSelectionRectVisible(True)
        self._view.set_message("No texture cache selected")
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.dragged.connect(self.drag_action)
        self._view.previewed.connect(self.toggle_preview_action)
        self._view.customContextMenuRequested.connect(self.context_action)
        self._view.verticalScrollBar().valueChanged.connect(self.scrolling_action)
        # a grid that has been resized, filtered or filled has a new band under
        # it without anything having scrolled
        self._view.verticalScrollBar().rangeChanged.connect(self.refill_action)

        self._inspector = InspectorPane()
        self._inspector.dragged.connect(self.inspector_drag_action)
        self._inspector.menued.connect(self.inspector_context_action)
        self._inspector.exported.connect(lambda format: self.export_action(format, False))
        self._inspector.outgrown.connect(self._settle.start)

        splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._view)
        splitter.addWidget(self._inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setCollapsible(0, False)

        # the toolbar and the window already draw lines above and beside the
        # grid, so its frame tucks those two edges out of sight
        tuck = -self._view.frameWidth()

        splitter.setContentsMargins(tuck, tuck, 0, 0)
        splitter.setSizes([self.width() - INSPECTOR_WIDTH, INSPECTOR_WIDTH])

        splitter.restoreState(stored_blob(settings, SPLITTER_KEY))

        self._splitter = splitter

        self.setCentralWidget(splitter)

        # laid over the central widget while a cache is held over the window
        self._zone = DropZone(self)

        # what the last thing held over the window would do if it were dropped,
        # since working that out reads the file and drag events arrive far too
        # fast to read it on every one of them
        self._offered: tuple[Path, str | None] | None = None

        self._filters = FilterBar(self)

        # the strip comes back up as it was left, before anything is listening,
        # so the colors are already on it when a cache arrives to be ranked
        self._filters.revive(settings.value(FILTERS_KEY))

        self._filters.changed.connect(self.filter_action)
        self._filters.matched.connect(self.match_action)
        self._filters.picking.connect(self.pick_action)
        self._filters.visibilityChanged.connect(self.filters_shown_action)

        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._filters)

        self._status = WindowStatus(self)

        self._actions = WindowActions(self, self.menuBar())
        self._actions.new_window.connect(self.new_window)
        self._actions.opened.connect(self.open_action)
        self._actions.reopened.connect(self.open_cache)
        self._actions.reloaded.connect(self.refresh_action)
        self._actions.exported.connect(self.export_action)
        self._actions.copied.connect(self.copy_action)
        self._actions.uuid_copied.connect(self.copy_uuid_action)
        self._actions.color_picked.connect(self.filter_color_action)
        self._actions.picture_picked.connect(self.pick_action)
        self._actions.picture_pasted.connect(self.paste_action)
        self._actions.filters_disabled.connect(self._filters.disable_action)
        self._actions.previewed.connect(self.preview_action)
        self._actions.preview_toggled.connect(self.toggle_preview_action)
        self._actions.inspected.connect(self.inspector_action)
        self._splitter.splitterMoved.connect(self.inspector_dragged_action)
        self._actions.filtered.connect(self.filters_action)
        self._actions.incompleted.connect(self.incomplete_action)
        self._actions.simple_shown.connect(self.simple_action)
        self._actions.abouted.connect(self.about_action)
        self._actions.close_window.connect(self.close)

        self._zoom = ZoomControl(self)
        self._status.add_control(self._zoom)

        # how much is selected and how much is in the cache both move around
        # under the menu, and only this end knows either of them
        self._actions.exports.aboutToShow.connect(self.sync_export)

        # the checkerboard is the app's rather than this window's, and a click in one
        # window is a repaint in every one of them
        CheckerboardChanges.shared().changed.connect(self.restyle)

        CellSizeChanges.shared().changed.connect(self.resize_cells)

        # both entries come up out of their stored settings, before anything is
        # listening to them, so the pane and the window are put where the menu
        # already says they are here
        self.sync_inspector()
        self.sync_preview()
        self.sync_filters()
        self.sync_find()
        self.sync_incomplete()
        self.sync_simple()

    def opened_cache(self) -> Path | None:
        return self._cache.cache_dir if self._cache is not None else None

    @classmethod
    def session(cls) -> list[Path]:
        return cls._app.session.stored()

    @classmethod
    def save_session(cls) -> None:
        cls._app.session.save()

    @classmethod
    def any_open(cls) -> bool:
        return cls._app.session.any_open()

    @classmethod
    def quitting(cls) -> None:
        cls._app.session.quit()

    @classmethod
    def shared_preview(cls) -> "PreviewWindow":
        """The one preview window, which belongs to the app rather than a window"""

        return cls._app.preview.shared()

    @classmethod
    def set_preview_shown(cls, shown: bool) -> None:
        """Say across the app whether the shared preview is up"""

        # the menus are moved without being listened to, so what they say the
        # preview is doing is put away here instead
        WindowActions.store_preview(shown)

        cls._app.preview.sync_ticks(shown=shown)

    @classmethod
    def follow_preview(cls, window: "MainWindow | None" = None) -> None:
        """Point the shared preview at a window, or at whichever one is left to take it"""

        cls._app.preview.follow(window)

    @classmethod
    def preview_closed_action(cls) -> None:
        cls.set_preview_shown(False)

    @classmethod
    def about(cls) -> None:
        cls._app.show_about(AboutDialog)

    @classmethod
    def set_app_menu(cls, menu: AppMenu) -> None:
        cls._app.menu = menu

    def save_layout(self) -> None:
        settings = QSettings()

        settings.setValue(GEOMETRY_KEY, self.saveGeometry())
        settings.setValue(SPLITTER_KEY, self._splitter.saveState())
        settings.setValue(FILTERS_KEY, self._filters.state())

        MainWindow._app.preview.save_geometry(settings)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_layout()

        self._actions.shutdown()
        self._zoom.shutdown()

        CheckerboardChanges.shared().changed.disconnect(self.restyle)
        CellSizeChanges.shared().changed.disconnect(self.resize_cells)

        if self._job is not None:
            self._job.shutdown()

        model = self._model

        if model is not None:
            model.shutdown()

        MainWindow._app.session.remove(self)

        menu = MainWindow._app.menu

        if menu is not None:
            menu.forget(self._actions)

        # the preview is left with whichever window is still open to take it
        if MainWindow._app.preview.release(self):
            MainWindow.follow_preview()

        if not MainWindow._app.session.quitting:
            MainWindow.save_session()

        super().closeEvent(event)

    def event(self, event: QEvent) -> bool:
        # a window dragged onto a screen of another resolution
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self.resize_cells()

        return super().event(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)

        if event.type() == QEvent.Type.PaletteChange:
            self.restyle()

        if event.type() == QEvent.Type.ActivationChange:
            self.activation_action()

    def activation_action(self) -> None:
        menu = MainWindow._app.menu

        if not self.isActiveWindow():
            if menu is not None:
                menu.resync(self._actions)

            return

        MainWindow.follow_preview(self)

        if menu is not None:
            menu.follow(self._actions)

    def restyle(self) -> None:
        sync_checkerboard()

        # both panes keep the texture's alpha and lay the checkerboard down
        # behind it, so a new checkerboard is a repaint rather than a fresh
        # decode. the checkerboard they draw is their own, and moves without the
        # cells' checkerboard moving with it
        preview = MainWindow._app.preview.window

        if preview is not None:
            preview.update()

        self.paint_inspector()

        model = self._model

        if model is None or not model.restyle():
            return

        # a cell has the checkerboard painted into it, so it is decoded again
        self._view.viewport().update()

        self._settle.start()

    def resize_cells(self) -> None:
        focus = self._view.focus()

        self._view.doItemsLayout()
        self._view.restore_anchor(focus)

        model = self._model

        if model is None or not model.resize_cells(self._view.devicePixelRatioF()):
            return

        self._view.viewport().update()

        # a gesture resizes the grid every frame, and decodes or a scan running
        # alongside slow each layout down. both wait until it settles
        model.hold(decodes=True)

        self._moving.start(ZOOM_SETTLE_MS)

    def open_action(self) -> None:
        dialog = QFileDialog(self, "Select a texturecache directory")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly)
        dialog.setFilter(QDir.Filter.AllDirs | QDir.Filter.Hidden | QDir.Filter.NoDotAndDotDot)
        dialog.setLabelText(QFileDialog.DialogLabel.Accept, "Open Cache")

        cache_dirs = [QUrl.fromLocalFile(d.parent) for d in suggested_paths()]
        dialog.setSidebarUrls(cache_dirs)

        if dialog.exec():
            self.open_cache(Path(dialog.selectedFiles()[0]))

    def refresh_action(self) -> None:
        if self._cache is None:
            return

        before = {texture.uuid: texture for texture in self._cache}

        try:
            changed = list(self._cache.refresh())
        except (FileNotFoundError, TextureCacheError) as e:
            warn(self, f"Could not reload {self._cache.cache_dir.name}.", str(e))
            return

        added = [texture for texture in changed if texture.uuid not in before]
        rewritten = [
            texture for texture in changed if texture.uuid in before and stamp(texture) != stamp(before[texture.uuid])
        ]
        evicted = [texture for uuid, texture in before.items() if uuid not in self._cache]

        for texture in rewritten + evicted:
            remove_cells(texture.uuid)
            remove_cells(sidebar_key(texture.uuid))

        forget_gone(self._known, self._cache)

        # an entry the grid is not showing is neither news to report nor a row
        # to scroll to or take away, however much of it the viewer wrote
        if not self.showing_incomplete():
            added = [texture for texture in added if texture.whole()]
            rewritten = [texture for texture in rewritten if texture.whole()]
            evicted = [texture for texture in evicted if texture.whole()]

        if not (added or rewritten or evicted):
            self._status.flash("No new textures found after reload")
            return

        self.repopulate(
            f"Reloaded {format_count(len(added))} new, {format_count(len(rewritten))} changed"
            f" and {format_count(len(evicted))} evicted textures",
            to_end=bool(added),
        )

    def export_action(self, format: Format, everything: bool) -> None:
        model = self._model

        if model is None or self._job is not None:
            return

        textures = self.export_textures(model, everything)

        if not textures:
            return

        out_dir = ask_for_directory(self, textures, format)

        if out_dir is not None:
            self.export(textures, out_dir, format, model.reads)

    def inspector_drag_action(self) -> None:
        self.drag_action(self._inspector)

    def drag_action(self, source: QWidget | None = None) -> None:
        model = self._model

        if model is None:
            return

        paths = self.stage_selection(model, "drag", while_held=True)

        if not paths:
            return

        pixmap = self.drag_pixmap(model)

        drag = QDrag(source if source is not None else self._view)
        drag.setMimeData(file_data(paths))

        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

        drag.exec(Qt.DropAction.CopyAction)

    def copy_action(self) -> None:
        model = self._model

        if model is None:
            return

        textures = self.export_textures(model, everything=False)

        if len(textures) == 1 and self.copy_image(model, textures[0]):
            self._status.flash("Copied texture to clipboard")
            return

        paths = self.stage_selection(model, "copy")

        if not paths:
            if len(textures) == 1:
                self._status.flash("Could not copy texture")

            return

        QGuiApplication.clipboard().setMimeData(file_data(paths))

        self._status.flash(f"Copied {format_count(len(paths))} texture(s) to clipboard")

    def copy_uuid_action(self) -> None:
        model = self._model

        if model is None:
            return

        textures = self.export_textures(model, everything=False)

        if len(textures) != 1:
            return

        QGuiApplication.clipboard().setText(textures[0].uuid)

        self._status.flash("Copied UUID to clipboard")

    def copy_image(self, model: TextureModel, texture: Texture) -> bool:
        QGuiApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

        try:
            with model.reads:
                codestream = texture.codestream()

            image = decode_image(codestream, POOL_THREADS)
        except (TextureCacheError, OSError):
            # an unfinished texture will not decode, and goes out as a file instead
            return False
        finally:
            QGuiApplication.restoreOverrideCursor()

        QGuiApplication.clipboard().setImage(image)

        return True

    def stage_selection(self, model: TextureModel, verb: str, *, while_held: bool = False) -> list[Path]:
        textures = self.export_textures(model, everything=False)

        if not textures:
            return []

        if len(textures) > DRAG_LIMIT:
            # a drag turned away still has the mouse down, so the alert waits
            # for the press to be over rather than coming up under it
            QTimer.singleShot(
                0,
                lambda: warn(
                    self,
                    f"Can't {verb} more than {format_count(DRAG_LIMIT)} textures at once.",
                    "Export them to a folder instead.",
                ),
            )
            return []

        # staging runs an event loop of its own, and a second one must not
        # start inside it
        if self._staging:
            return []

        self._staging = True
        QGuiApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

        try:
            return staged(self, textures, model.reads, title=verb.title(), while_held=while_held)
        finally:
            QGuiApplication.restoreOverrideCursor()
            self._staging = False

    def drag_pixmap(self, model: TextureModel) -> QPixmap:
        index = self._view.selected_index()

        if not index.isValid():
            return QPixmap()

        selected = self._view.selectionModel().selectedIndexes()

        return stack_pixmap(grid_cards(model, stack_textures(model, index, selected)))

    def context_action(self, at: QPoint) -> None:
        model = self._model

        if model is None:
            return

        selection = self._view.selectionModel()
        index = self._view.indexAt(at)

        at = self._view.viewport().mapToGlobal(at)

        if not index.isValid():
            # finder lets go of the selection on a click between items, and
            # what is left to offer is the grid's own setting
            selection.clearSelection()

            menu = self._actions.grid_menu(self._view)
            menu.exec(at)
            menu.deleteLater()

            return

        if not selection.isSelected(index):
            selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)

        self.show_context_menu(self._view, at)

    def inspector_context_action(self, at: QPoint) -> None:
        self.show_context_menu(self._inspector, at)

    def show_context_menu(self, parent: QWidget, at: QPoint) -> None:
        if self._model is None:
            return

        selected = len(self._view.selectionModel().selectedIndexes())

        if not selected:
            return

        menu = self._actions.context_menu(
            parent,
            selected,
            idle=self._job is None,
            previewing=self.holds_preview(),
        )

        menu.exec(at)
        menu.deleteLater()

    def sync_export(self) -> None:
        model = self._model

        selected = len(self._view.selectionModel().selectedIndexes()) if model is not None else 0
        total = model.rowCount() if model is not None else 0

        self._actions.sync_export(selected, total, idle=self._job is None)
        self._inspector.set_exportable(self._job is None)

        # the selection moves without the menu being opened, and cmd+c is
        # answered by whichever bar is up
        menu = MainWindow._app.menu

        if menu is not None:
            menu.resync(self._actions)

    def export_textures(self, model: TextureModel, everything: bool) -> list[Texture]:
        if everything:
            return [model.texture(row) for row in range(model.rowCount())]

        rows = sorted(index.row() for index in self._view.selectionModel().selectedIndexes())

        return [model.texture(row) for row in rows]

    def export(self, textures: list[Texture], out_dir: Path, format: Format, reads: Lock) -> None:
        self._job = ExportRun(self, textures, out_dir, format, reads, done=self.exported)

        # a refresh reads the cache's files in again and puts the new ones in
        # place of the ones the export is halfway through reading
        self._actions.reload.setEnabled(False)
        self.sync_export()

        self._job.start()

    def exported(self, summary: str) -> None:
        self._job = None

        self._actions.reload.setEnabled(self._cache is not None)
        self.sync_export()

        self._status.flash(summary)

    def filters_action(self, shown: bool) -> None:
        self.sync_filters()

    def filters_shown_action(self, shown: bool) -> None:
        # the bar can also be hidden from the context menu the window puts up
        # over its toolbars, and the entry under the menu is what has to say so
        # afterwards. a bar away because no cache is open is not that
        if self._cache is not None:
            self._actions.filters.setChecked(shown)

    def sync_filters(self) -> None:
        opened = self._cache is not None

        self._actions.filters.setEnabled(opened)
        self._filters.setVisible(opened and self._actions.filters.isChecked())

    def filter_color_action(self) -> None:
        """Pick a colour off the menu, with the strip up to show what was picked"""

        if self._filters.add_action():
            self._actions.filters.setChecked(True)

    def pick_action(self) -> None:
        """Pick a picture off disk to search the cache for"""

        path, _ = QFileDialog.getOpenFileName(self, "Match Image", QDir.homePath(), image_filter())

        if path:
            self.match_file(Path(path))

    def match_file(self, path: Path) -> None:
        image = image_file(path)

        if image.isNull():
            warn(self, f"Could not read {path.name} as an image.")
            return

        self.match_picture(image, path.name)

    def paste_action(self) -> None:
        image = QGuiApplication.clipboard().image()

        if image.isNull():
            warn(self, "There is no image on the clipboard.", "Copy a picture and try again.")
            return

        self.match_picture(image, "the pasted image")

    def match_picture(self, image: QImage, name: str) -> None:
        # the chip is what says a picture is being asked for, so the strip comes
        # up if it was away, rather than the grid reordering itself for no
        # reason anyone can see
        self._actions.filters.setChecked(True)

        self._filters.set_reference(image, name)

    def match_action(self) -> None:
        """Take up, or let go of, the picture the strip is holding"""

        self.sync_find()

        model = self._model

        if model is None:
            return

        if model.set_reference(self._filters.reference()):
            self.ranked_action()
        else:
            # the scan that has to answer for the picture is still out, and what
            # was asked for here is applied the moment it lands
            self._status.flash("Please wait...")

    def sync_find(self) -> None:
        self._actions.sync_find(opened=self._cache is not None, asking=self._filters.asking())

    def incomplete_action(self, shown: bool) -> None:
        model = self._model

        if model is None:
            return

        self.hiding_applied(applied=model.set_incomplete_hidden(not shown))

    def sync_incomplete(self) -> None:
        self._actions.incomplete.setEnabled(self._cache is not None)

    def showing_incomplete(self) -> bool:
        return self._actions.incomplete.isChecked()

    def simple_action(self, shown: bool) -> None:
        model = self._model

        if model is None:
            return

        self.hiding_applied(applied=model.set_simple_hidden(not shown))

    def hiding_applied(self, *, applied: bool) -> None:
        if not applied:
            self._status.set_summary(SCANNING_MESSAGE)
            return

        self.sync_empty()
        self.sync_export()

        self._status.set_summary(self.summary())

        # the reset keeps the view on the texture it was on, which a ranking
        # takes it off again
        if self.ranking():
            self.scroll_ranked()

    def sync_simple(self) -> None:
        self._actions.simple.setEnabled(self._cache is not None)

    def showing_simple(self) -> bool:
        return self._actions.simple.isChecked()

    def narrowed(self) -> bool:
        model = self._model

        return model is not None and model.narrowed

    def ranking(self) -> bool:
        """Whether the grid is in an order something asked for, rather than the cache's"""

        return bool(self._filters.colors()) or self._filters.reference() is not None

    def filter_action(self, colors: list[QColor]) -> None:
        self.sync_find()

        model = self._model

        if model is None:
            return

        if model.set_filters(colors):
            self.ranked_action()
        else:
            self._status.flash("Please wait...")

    def ranked_action(self) -> None:
        self.sync_empty()
        self.sync_export()

        self._status.set_summary(self.summary())

        model = self._model

        if model is not None and model.matching:
            self.show_ranking()
        else:
            self.scroll_ranked()

    def thinned_action(self) -> None:
        self.sync_empty()
        self.sync_export()

        self._status.set_summary(self.summary())

    def show_ranking(self) -> None:
        """Take the grid to the top of the ranking, where the likest textures are

        Nothing is selected there. A ranking is a reordering of the grid rather
        than an answer to pick out of it, so which row the user ends up on stays
        theirs to say.
        """

        if self._model is None:
            return

        self._view.unpin()
        self._view.scrollToTop()

    def summary(self) -> str:
        model = self._model
        if model is not None and model.scanning:
            return SCANNING_MESSAGE

        if model is None or not model.narrowed:
            return self.grid_summary()

        if model.matching:
            return ranked_summary(model, self._filters.reference_name())

        return narrowed_summary(model)

    def grid_summary(self) -> str:
        model = self._model

        if self._cache is None or model is None:
            return self._summary

        return summary_of(model, len(self._cache), counting_incomplete=self.showing_incomplete())

    def sync_empty(self) -> None:
        model = self._model

        self._view.set_message(empty_message(model))

    def scroll_ranked(self) -> None:
        current = self._view.currentIndex()

        if current.isValid():
            self._view.unpin()
            self._view.scrollTo(current, QListView.ScrollHint.PositionAtCenter)
        elif self.narrowed():
            self._view.unpin()
            self._view.scrollToTop()
        else:
            self.scroll_to_end()

    def inspector_action(self, shown: bool) -> None:
        self.sync_inspector()

        if shown:
            self.fill_inspector()

    def sync_inspector(self) -> None:
        opened = self._cache is not None

        self._actions.inspector.setEnabled(opened)
        self._inspector.setVisible(opened and self._actions.inspector.isChecked())

        # a pane dragged shut stays shut when shown again, so it opens back up
        # at its usual width
        if self._inspector.isVisible() and self._splitter.sizes()[1] == 0:
            whole = sum(self._splitter.sizes())
            self._splitter.setSizes([whole - INSPECTOR_WIDTH, INSPECTOR_WIDTH])

    def inspector_dragged_action(self) -> None:
        # dragging the hairline all the way over hides the pane, and the menu
        # has to say so or there is no way back to it
        if self._inspector.isVisible() and self._splitter.sizes()[1] == 0:
            self._actions.inspector.setChecked(False)

    def preview_action(self, shown: bool) -> None:
        # one window's menu says whether the shared window is up, so the rest of
        # them are put where it says without being asked to do this again
        MainWindow.set_preview_shown(shown)

        self.sync_preview()

    def toggle_preview_action(self) -> None:
        if self.holds_preview():
            self._actions.preview.setChecked(False)
            return

        if not self._view.has_selection():
            return

        self.open_preview_action()

    def open_preview_action(self) -> None:
        self._actions.preview.setChecked(True)

        MainWindow.follow_preview(self)

        preview = MainWindow._app.preview.window

        if preview is not None:
            preview.present()

    def wants_preview(self) -> bool:
        return self._cache is not None and self._actions.preview.isChecked()

    def holds_preview(self) -> bool:
        return self.wants_preview() and MainWindow._app.preview.followed_by(self)

    def preview_menu_entry(self) -> QAction:
        return self._actions.preview

    def sync_preview(self) -> None:
        self._actions.preview.setEnabled(self._cache is not None)

        MainWindow.follow_preview(self)

    def preview_ready_action(self, _uuid: str) -> None:
        self.fill_preview()

    def fill_preview(self) -> None:
        """Show whatever is selected in the preview window, if it is following this one"""

        model = self._model
        preview = MainWindow._app.preview.window

        if not MainWindow._app.preview.followed_by(self) or preview is None or not preview.isVisible():
            return

        if model is None:
            return

        index = self._view.selected_index()

        if not index.isValid():
            preview.clear()
            return

        texture = model.texture(index.row())

        # asking marks this as the one texture the window is on, so a decode
        # the selection has already been walked past is dropped when it lands.
        # the grid and the pane read smaller and sooner, and whichever of them
        # has already been through this texture stands in until it lands
        preview.show_texture(texture, model.preview(texture), model.stand_in(texture))

    def selection_action(self, *_: object) -> None:
        self.sync_pane_tone()
        self.fill_inspector()
        self.fill_preview()
        self.sync_export()
        self.sync_selection()

    def sync_pane_tone(self) -> None:
        model = self._model
        index = self._view.selected_index()

        standing = model.texture(index.row()).uuid if model is not None and index.isValid() else ""

        # growing a selection, or walking the current index around inside one, is
        # still the same texture in the panes
        if standing == self._standing:
            return

        self._standing = standing

        reset_pane_tone()

    def ready_action(self, uuid: str) -> None:
        if any(texture.uuid == uuid for texture in self._stack):
            self.paint_inspector()

    def settle_action(self) -> None:
        model = self._model

        if model is None:
            return

        room = self._inspector.pile_room()

        for texture in self._stack:
            model.full_decode(texture, room)

        self.paint_inspector()

    def fill_inspector(self) -> None:
        model = self._model

        if not self._inspector.isVisible() or model is None:
            return

        index = self._view.selected_index()

        if not index.isValid():
            self._stack = []
            self._inspector.clear()

            # with nothing shown there is nothing for the automatic checkerboard to
            # have been measured against
            set_picked_lightness(None)
            return

        selected = self._view.selectionModel().selectedIndexes()

        self._inspector.show_texture(
            model.texture(index.row()),
            len(selected),
            sum(model.texture(other.row()).image_size for other in selected),
        )

        self._stack = stack_textures(model, index, selected)
        self._settle.start()

        self.paint_inspector()

    def paint_inspector(self) -> None:
        model = self._model

        if model is not None:
            paint_pane(self._inspector, model, self._stack)

    def refill_action(self) -> None:
        model = self._model

        if model is not None:
            prefetch(self._view, model, force=True)

    def scrolling_action(self) -> None:
        model = self._model

        if model is None:
            return

        model.hold(decodes=False)
        prefetch(self._view, model)

        self._moving.start(SCROLL_SETTLE_MS)

    def settled_action(self) -> None:
        model = self._model

        if model is not None:
            model.release()

        self.refill_action()

    def scroll_to_end(self) -> None:
        self._view.pin_to_bottom()

    def open_cache(self, cache_dir: Path, *, replace: bool = False) -> None:
        try:
            cache = TextureCache(cache_dir)
        except (FileNotFoundError, TextureCacheError) as e:
            warn(self, f"Could not open {cache_dir}.", str(e))
            return

        RecentCaches.shared().remember(cache.cache_dir)

        if replace or self._cache is None:
            self.set_cache(cache)
        else:
            self.new_window(cache)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        dropped = self.dropped_path(event)

        if dropped is None:
            return

        offer = self.offer_for(dropped)

        if offer is None:
            return

        actions = event.possibleActions()

        if actions & Qt.DropAction.LinkAction:
            event.setDropAction(Qt.DropAction.LinkAction)
        elif actions & Qt.DropAction.CopyAction:
            event.setDropAction(Qt.DropAction.CopyAction)
        else:
            return

        event.accept()

        central = self.centralWidget()

        if central is not None:
            self._zone.offer(offer, central.geometry())

    def dropped_path(self, event: QDropEvent) -> Path | None:
        """The one file or directory being held over the window, if it is only one"""

        if event.source() is not None:
            # a texture dragged out of any of the app's windows should not be
            # considered a drop target
            return None

        urls = event.mimeData().urls()

        if self._job is not None or len(urls) != 1:
            return None

        return Path(urls[0].toLocalFile())

    def offer_for(self, dropped: Path) -> str | None:
        """What dropping this would do, and nothing when it would do nothing

        Remembered until the drag ends, since a drag over the window is a run
        of events about the one path and answering takes a look at the disk.
        """

        if self._offered is None or self._offered[0] != dropped:
            self._offered = (dropped, self.offer_of(dropped))

        return self._offered[1]

    def offer_of(self, dropped: Path) -> str | None:
        """A directory is a cache to open, and a picture is one to search an open cache for"""

        if dropped.is_dir():
            return f"Drop to open {dropped.name}"

        if self._cache is not None and readable_image(dropped):
            return f"Drop to match {dropped.name}"

        return None

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._zone.withdraw()
        self.forget_offer()

        super().dragLeaveEvent(event)

    def forget_offer(self) -> None:
        # whatever comes over the window next is a fresh question, and the file
        # this one was about may not even be the same file by then
        self._offered = None

    def dropEvent(self, event: QDropEvent) -> None:
        self._zone.withdraw()

        dropped = self.dropped_path(event)
        offered = dropped is not None and self.offer_for(dropped) is not None

        self.forget_offer()

        if dropped is None or not offered:
            return

        event.accept()

        if dropped.is_dir():
            self.open_cache(dropped, replace=True)
        else:
            self.match_file(dropped)

    def new_window(self, cache: TextureCache | None = None) -> "MainWindow":
        window = MainWindow()

        window.move(self.pos() + NEW_WINDOW_OFFSET)

        if cache is not None:
            window.set_cache(cache)

        window.show()

        return window

    def set_cache(self, cache: TextureCache) -> None:
        self._cache = cache
        self._known = {}

        self._actions.reload.setEnabled(True)
        self.sync_inspector()
        self.sync_preview()
        self.sync_filters()
        self.sync_find()
        self.sync_incomplete()
        self.sync_simple()
        self._status.set_opened(True)

        MainWindow.save_session()

        self.setWindowTitle(f"{cache.cache_dir} — {APP_DISPLAY_NAME}")
        self.setWindowFilePath(str(cache.cache_dir))
        self.populate_grid()

    def sync_selection(self) -> None:
        model = self._model

        if model is None:
            return

        self._status.show_selection(len(self._view.selectionModel().selectedIndexes()), model.rowCount())

    def repopulate(self, note: str | None = None, *, to_end: bool) -> None:
        selection, scroll = self._view.kept_selection(), self._view.kept_scroll()

        self.populate_grid(note)

        self._view.restore_selection(selection)

        # a ranking has already put the view at the top
        if self.ranking():
            return

        if to_end:
            self.scroll_to_end()
        else:
            self._view.restore_scroll(scroll)

    def populate_grid(self, note: str | None = None) -> None:
        if self._cache is None:
            return

        old_model = self._view.model()

        if isinstance(old_model, TextureModel):
            old_model.shutdown()

        model = TextureModel(
            list(self._cache),
            self._cache,
            self._known,
            self._view.devicePixelRatioF(),
            self,
            incomplete_hidden=not self.showing_incomplete(),
        )
        model.full_ready.connect(self.ready_action)
        model.preview_ready.connect(self.preview_ready_action)
        model.ranked.connect(self.ranked_action)
        model.thinned.connect(self.thinned_action)

        self._model = model

        self._view.setModel(model)

        if old_model is not None:
            old_model.deleteLater()

        selection = self._view.selectionModel()
        selection.selectionChanged.connect(self.selection_action)
        selection.currentChanged.connect(self.selection_action)

        self._stack = []
        self._inspector.clear()

        # the window was showing a texture out of the model just retired, and
        # is filled again by whatever selection lands in the new one
        preview = MainWindow._app.preview.window

        if preview is not None and MainWindow._app.preview.followed_by(self):
            preview.clear()

        model.set_simple_hidden(not self.showing_simple())
        model.set_reference(self._filters.reference())
        model.set_filters(self._filters.colors())

        self.sync_empty()
        self.sync_export()

        if model.matching:
            self.show_ranking()
        elif not self.ranking():
            self.scroll_to_end()

        self._summary = self.grid_summary()

        self._status.set_summary(note if note else self.summary())

    def about_action(self) -> None:
        MainWindow.about()


# a preview closed by hand puts every window's menu tick down, which is the one
# thing the app-wide state cannot do for itself: the ticks belong to the menus
MainWindow._app.preview_closed = MainWindow.preview_closed_action
