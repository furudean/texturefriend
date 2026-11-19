import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSignalBlocker, Qt, Signal, SignalInstance
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QMenu, QMenuBar, QWidget

from texturefriend import APP_DISPLAY_NAME
from texturefriend.cache.export import (
    FORMATS,
    ExportFormatChanges,
    Format,
    export_format,
    set_export_format,
)
from texturefriend.cache.recents import RecentCaches
from texturefriend.cache.suggested import paths as suggested_paths
from texturefriend.grid.sorting import (
    ORDER_LABELS,
    Order,
    SortChanges,
    order_label,
    set_sort_descending,
    set_sort_order,
    sort_descending,
    sort_order,
)
from texturefriend.view.cellsize import (
    DEFAULT_CELL_SIZE,
    CellSizeChanges,
    can_step,
    cell_size,
    set_cell_size,
    step_cell_size,
)
from texturefriend.view.checkerboard import CheckerboardChanges, CheckerTone, grid_tone, set_grid_tone
from texturefriend.view.formatting import format_count

PREVIEW_KEY = "showPreview"
INSPECTOR_KEY = "showInspector"
FILTERS_KEY = "showColorFilters"
INCOMPLETE_KEY = "showIncomplete"
SIMPLE_KEY = "showSimple"

EXPORT_KEY = QKeySequence("Ctrl+E")

UUID_KEY = QKeySequence("Ctrl+Alt+C" if sys.platform == "darwin" else "Ctrl+Shift+C")

TONES = {
    CheckerTone.AUTO: ("&Automatic", "Match the checkerboard to the window's own colours"),
    CheckerTone.LIGHT: ("&Checkerboard (Light)", "Draw a light checkerboard behind the transparent parts of a texture"),
    CheckerTone.DARK: ("&Checkerboard (Dark)", "Draw a dark checkerboard behind the transparent parts of a texture"),
    CheckerTone.NONE: ("&None", "Leave the transparent parts of a texture as they are"),
}


FORWARDED = (
    "new_window",
    "opened",
    "reopened",
    "reloaded",
    "exported",
    "copied",
    "uuid_copied",
    "color_picked",
    "picture_picked",
    "picture_pasted",
    "filters_disabled",
    "preview_toggled",
    "close_window",
    "abouted",
)
"""What the app-wide bar hands on to the window in front, as it was asked

The ticks are not among them: one of those is moved on the window's own entry
instead, so that its menu goes on saying what its panes are doing.
"""

MIRRORED = ("reload", "copy", "copy_uuid", "filter_color", "match", "paste", "disable")
"""The entries a window enables and disables as it goes, which the app-wide bar copies"""


def store_toggle(key: str, shown: bool) -> None:
    """Remember where a tick was left

    `shown` is positional because this is connected to `QAction.toggled`,
    which hands its argument over that way.
    """

    QSettings().setValue(key, shown)


def stored_toggle(key: str, *, default: bool) -> bool:
    return bool(QSettings().value(key, default, type=bool))


@dataclass(frozen=True)
class Toggle:
    """One tick under the View menu

    They are all the same entry with a different label: checkable, put away
    and read back under a key of its own, and reported to the window that has
    to act on it.
    """

    key: str
    label: str
    tip: str
    on: bool = False
    shortcut: str | None = None

    def build(self, owner: QWidget, reports: SignalInstance) -> QAction:
        entry = QAction(self.label, owner)

        if self.shortcut is not None:
            entry.setShortcut(QKeySequence(self.shortcut))

        entry.setStatusTip(self.tip)
        entry.setCheckable(True)
        entry.setChecked(stored_toggle(self.key, default=self.on))

        # a partial rather than a bound method of this Toggle: Qt keeps only a
        # weak reference to a bound method's object, and the table these are
        # built from is gone by the time the first tick is moved
        entry.toggled.connect(partial(store_toggle, self.key))
        entry.toggled.connect(reports)

        return entry


def triggers(entry: QAction, call: Callable[[], object]) -> None:
    entry.triggered.connect(lambda _checked=False: call())


def export_title(count: int, *, everything: bool) -> str:
    if everything:
        return "Export Full Cache As"

    if count == 1:
        return "Export As"

    return f"Export {format_count(count)} Selected As" if count else "Export Selected As"


class WindowActions(QObject):
    new_window = Signal()
    opened = Signal()
    reopened = Signal(Path)
    reloaded = Signal()
    exported = Signal(Format, bool)
    copied = Signal()
    uuid_copied = Signal()
    color_picked = Signal()
    picture_picked = Signal()
    picture_pasted = Signal()
    filters_disabled = Signal()
    inspected = Signal(bool)
    filtered = Signal(bool)
    previewed = Signal(bool)
    preview_toggled = Signal()
    incompleted = Signal(bool)
    simple_shown = Signal(bool)
    abouted = Signal()
    close_window = Signal()

    def __init__(self, owner: QWidget, menu: QMenuBar) -> None:
        super().__init__(owner)

        # the entries belong to the owner rather than to this, since a shortcut
        # is only answered by a window the action can be reached from
        self.build_toggles(owner)
        self.build_sort(owner)

        self.build_file_menu(owner, menu.addMenu("&File"))
        self.build_edit_menu(owner, menu.addMenu("&Edit"))
        self.build_export_menu(owner, menu.addMenu("&Export"))
        self.build_filter_menu(owner, menu.addMenu("Fi&lter"))
        self.build_sort_menu(menu.addMenu("&Sort"))
        self.build_view_menu(owner, menu.addMenu("&View"))
        self.build_app_menu(menu.addMenu("About"))

    def build_file_menu(self, owner: QWidget, file_menu: QMenu) -> None:
        new = QAction("&New Window", owner)
        new.setShortcut(QKeySequence(QKeySequence.StandardKey.New))
        new.setStatusTip("Open another window")
        triggers(new, self.new_window.emit)

        open = QAction("&Open...", owner)
        open.setShortcut(QKeySequence(QKeySequence.StandardKey.Open))
        open.setStatusTip("Open a texture cache")
        triggers(open, self.opened.emit)

        # the standard refresh key is f5 off mac, which we want to rewrite
        refresh_keys = [*QKeySequence.keyBindings(QKeySequence.StandardKey.Refresh), QKeySequence("Ctrl+R")]

        self.reload = QAction("&Reload", owner)
        self.reload.setShortcuts(list(dict.fromkeys(refresh_keys)))
        self.reload.setStatusTip("Reload the open texture cache from disk")
        self.reload.setEnabled(False)
        triggers(self.reload, self.reloaded.emit)

        close = QAction("&Close Window", owner)
        close.setShortcut(QKeySequence(QKeySequence.StandardKey.Close))
        close.setStatusTip("Close this window")
        triggers(close, self.close_window.emit)

        file_menu.addAction(open)

        self._recents = file_menu.addMenu("Open &Recent")
        self._suggested = file_menu.addMenu("Open &Suggested")

        file_menu.addSeparator()
        file_menu.addAction(self.reload)

        file_menu.addSeparator()
        file_menu.addAction(new)
        file_menu.addAction(close)

        RecentCaches.shared().changed.connect(self.populate_recents)

        self.populate_recents()
        self.populate_suggested()

    def build_edit_menu(self, owner: QWidget, edit_menu: QMenu) -> None:
        self.copy = QAction("&Copy", owner)
        self.copy.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        self.copy.setStatusTip("Put the selected texture on the clipboard, or several as files in the default format")
        self.copy.setEnabled(False)
        triggers(self.copy, self.copied.emit)

        self.copy_uuid = QAction("Copy &UUID", owner)
        self.copy_uuid.setShortcut(UUID_KEY)
        self.copy_uuid.setStatusTip("Put the selected texture's uuid on the clipboard")
        self.copy_uuid.setEnabled(False)
        triggers(self.copy_uuid, self.uuid_copied.emit)

        edit_menu.addAction(self.copy)
        edit_menu.addAction(self.copy_uuid)

    def build_export_menu(self, owner: QWidget, exports: QMenu) -> None:
        self.exports = exports

        self._selected_export = self.format_menu(exports, "Export Selected As", everything=False)
        self._all_export = self.format_menu(exports, "Export Full Cache As", everything=True)

        self._formats: dict[str, QAction] = {}

        formats = QActionGroup(owner)
        formats.setExclusive(True)

        for format in FORMATS:
            entry = QAction(format.label, owner)
            entry.setStatusTip(
                f"Write textures out as {format.label} when dragged, copied or exported with the shortcut"
            )
            entry.setCheckable(True)
            entry.setActionGroup(formats)
            triggers(entry, partial(set_export_format, format))

            self._formats[format.suffix] = entry

        exports.addSeparator()
        exports.addMenu("&Default Format").addActions(list(self._formats.values()))

        ExportFormatChanges.shared().changed.connect(self.sync_export_format)

        self.sync_export_format()

        # how much is selected and how much is in the cache both move around
        # under the menu, so the two entries are named on the way open. there is
        # neither of them to count yet, and the titles are what the entries come
        # up as until a window has something to say about it
        self.sync_export(0, 0, idle=True)

    def build_filter_menu(self, owner: QWidget, filter_menu: QMenu) -> None:
        self.filter_color = QAction("&Color...", owner)
        self.filter_color.setStatusTip("Add a color to filter textures by")
        self.filter_color.setEnabled(False)
        triggers(self.filter_color, self.color_picked.emit)

        self.match = QAction("&Image...", owner)
        self.match.setShortcut(QKeySequence(QKeySequence.StandardKey.Find))
        self.match.setStatusTip("Show the textures that look like a picture on disk")
        self.match.setEnabled(False)
        triggers(self.match, self.picture_picked.emit)

        self.paste = QAction("Clip&board Image", owner)
        self.paste.setShortcut(QKeySequence(QKeySequence.StandardKey.Paste))
        self.paste.setStatusTip("Show the textures that look like the picture in the clipboard")
        self.paste.setEnabled(False)
        triggers(self.paste, self.picture_pasted.emit)

        self.disable = QAction("&Disable Filters", owner)
        self.disable.setStatusTip("Put down whatever the grid is being asked for")
        self.disable.setEnabled(False)
        triggers(self.disable, self.filters_disabled.emit)

        filter_menu.addAction(self.filter_color)
        filter_menu.addAction(self.match)
        filter_menu.addAction(self.paste)

        filter_menu.addSeparator()
        filter_menu.addAction(self.incomplete)
        filter_menu.addAction(self.simple)

        filter_menu.addSeparator()
        filter_menu.addAction(self.disable)

    def build_sort_menu(self, sort_menu: QMenu) -> None:
        sort_menu.addActions(list(self._orders.values()))

        sort_menu.addSeparator()
        sort_menu.addAction(self.ascending)
        sort_menu.addAction(self.descending)

    def build_view_menu(self, owner: QWidget, view_menu: QMenu) -> None:
        self.build_zoom(owner)
        self.build_tones(owner)

        view_menu.addAction(self.preview)
        view_menu.addAction(self.inspector)
        view_menu.addAction(self.filters)

        view_menu.addSeparator()
        view_menu.addAction(self.zoom_in)
        view_menu.addAction(self.zoom_out)
        view_menu.addAction(self.zoom_reset)

        view_menu.addSeparator()
        checkerboard = view_menu.addMenu("&Alpha Mode")
        checkerboard.addActions(list(self._tones.values()))

        view_menu.addSeparator()
        # any native system menus under here

    def build_toggles(self, owner: QWidget) -> None:
        # the command key "Ctrl" on mac and hands "Meta" to control
        inspector_key = "Shift+Ctrl+I" if sys.platform == "darwin" else "Shift+Alt+I"

        toggles = (
            Toggle(PREVIEW_KEY, "Show &Preview Pane", "Show the selected texture in a window of its own"),
            Toggle(
                INSPECTOR_KEY,
                "Show &Inspector",
                "Show details of the selected texture beside the grid",
                on=True,
                shortcut=inspector_key,
            ),
            Toggle(FILTERS_KEY, "Show &Filters", "Show the color filter bar", on=True),
            Toggle(
                INCOMPLETE_KEY,
                "Show &Incomplete Textures",
                "List entries the cache never finished downloading",
            ),
            Toggle(
                SIMPLE_KEY,
                "Show &Simple Textures",
                "List textures that are one solid color or fully transparent",
            ),
        )

        reports = {
            PREVIEW_KEY: self.previewed,
            INSPECTOR_KEY: self.inspected,
            FILTERS_KEY: self.filtered,
            INCOMPLETE_KEY: self.incompleted,
            SIMPLE_KEY: self.simple_shown,
        }

        self._toggles = {toggle.key: toggle.build(owner, reports[toggle.key]) for toggle in toggles}

        self.preview = self._toggles[PREVIEW_KEY]
        self.inspector = self._toggles[INSPECTOR_KEY]
        self.filters = self._toggles[FILTERS_KEY]
        self.incomplete = self._toggles[INCOMPLETE_KEY]
        self.simple = self._toggles[SIMPLE_KEY]

    def build_sort(self, owner: QWidget) -> None:
        self._orders: dict[Order, QAction] = {}

        orders = QActionGroup(owner)
        orders.setExclusive(True)

        for order, label in ORDER_LABELS.items():
            entry = QAction(label, owner)
            entry.setCheckable(True)
            entry.setActionGroup(orders)
            triggers(entry, partial(set_sort_order, order))

            self._orders[order] = entry

        self.cache_order = self._orders[Order.CACHE]

        directions = QActionGroup(owner)
        directions.setExclusive(True)

        self.ascending = QAction("&Ascending", owner)
        self.ascending.setCheckable(True)
        self.ascending.setActionGroup(directions)
        triggers(self.ascending, partial(set_sort_descending, False))

        self.descending = QAction("&Descending", owner)
        self.descending.setCheckable(True)
        self.descending.setActionGroup(directions)
        triggers(self.descending, partial(set_sort_descending, True))

        SortChanges.shared().changed.connect(self.sync_sort)

        self.sync_sort()

    def build_zoom(self, owner: QWidget) -> None:
        in_keys = [*QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn), QKeySequence("Ctrl+=")]

        self.zoom_in = QAction("Zoom &In", owner)
        self.zoom_in.setShortcuts(list(dict.fromkeys(in_keys)))
        self.zoom_in.setStatusTip("Draw textures larger")
        triggers(self.zoom_in, partial(step_cell_size, 1))

        self.zoom_out = QAction("Zoom &Out", owner)
        self.zoom_out.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut))
        self.zoom_out.setStatusTip("Draw textures smaller")
        triggers(self.zoom_out, partial(step_cell_size, -1))

        self.zoom_reset = QAction("&Reset Zoom", owner)
        self.zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        self.zoom_reset.setStatusTip("Draw textures at the original size")
        triggers(self.zoom_reset, partial(set_cell_size, DEFAULT_CELL_SIZE))

        CellSizeChanges.shared().changed.connect(self.sync_zoom)

        self.sync_zoom()

    def build_tones(self, owner: QWidget) -> None:
        self._tones = {}

        tones = QActionGroup(owner)
        tones.setExclusive(True)

        for tone, (label, tip) in TONES.items():
            entry = QAction(label, owner)
            entry.setStatusTip(tip)
            entry.setCheckable(True)
            entry.setActionGroup(tones)
            triggers(entry, partial(set_grid_tone, tone))

            self._tones[tone] = entry

        CheckerboardChanges.shared().changed.connect(self.sync_checkerboard)

        self.sync_checkerboard()

    def build_app_menu(self, app_menu: QMenu) -> None:
        # the label does not matter on macOS, where the role decides where it goes
        about = app_menu.addAction(f"About {APP_DISPLAY_NAME}")
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self.abouted.emit)

    def shutdown(self) -> None:
        RecentCaches.shared().changed.disconnect(self.populate_recents)
        CheckerboardChanges.shared().changed.disconnect(self.sync_checkerboard)
        CellSizeChanges.shared().changed.disconnect(self.sync_zoom)
        ExportFormatChanges.shared().changed.disconnect(self.sync_export_format)
        SortChanges.shared().changed.disconnect(self.sync_sort)

    def sync_zoom(self) -> None:
        # the ladder has ends, and an entry that would do nothing says so
        self.zoom_in.setEnabled(can_step(1))
        self.zoom_out.setEnabled(can_step(-1))
        self.zoom_reset.setEnabled(cell_size() != DEFAULT_CELL_SIZE)

    def sync_checkerboard(self) -> None:
        self._tones[grid_tone()].setChecked(True)

    def sync_sort(self) -> None:
        order = sort_order()

        self._orders[order].setChecked(True)

        (self.descending if sort_descending() else self.ascending).setChecked(True)

        # the cache's own order has no direction to it
        self.ascending.setEnabled(order is not Order.CACHE)
        self.descending.setEnabled(order is not Order.CACHE)

    def sync_export_format(self) -> None:
        current = export_format()

        self._formats[current.suffix].setChecked(True)

        # the shortcut sits on the default format's entry, so the menu shows
        # what it writes. only the entry under the menu bar answers it, since
        # the context menu builds its own copy of these and two answers to a
        # key is none
        for format, entry in zip(FORMATS, self._selected_export.actions(), strict=True):
            entry.setShortcut(EXPORT_KEY if format == current else QKeySequence())

    def ticks(self) -> tuple[str, ...]:
        return tuple(self._toggles)

    def tick(self, key: str) -> QAction:
        return self._toggles[key]

    def mirror(self, source: "WindowActions | None") -> None:
        for name in MIRRORED:
            entry: QAction = getattr(self, name)
            entry.setEnabled(source is not None and getattr(source, name).isEnabled())

        for key, entry in self._toggles.items():
            other = source.tick(key) if source is not None else None

            entry.setEnabled(other is not None and other.isEnabled())

            # copied rather than toggled: the window this comes from has
            # already acted on it, and is not to be told to do so again
            with QSignalBlocker(entry):
                entry.setChecked(other is not None and other.isChecked())

        if source is not None:
            self.cache_order.setText(source.cache_order.text())

        for entries, name in ((self._selected_export, "_selected_export"), (self._all_export, "_all_export")):
            theirs: QMenu | None = getattr(source, name) if source is not None else None

            entries.setEnabled(theirs is not None and theirs.isEnabled())

            if theirs is not None:
                entries.setTitle(theirs.title())

    def format_menu(self, parent: QMenu, title: str, *, everything: bool) -> QMenu:
        menu = parent.addMenu(title)

        for format in FORMATS:
            entry = menu.addAction(format.label)
            entry.setStatusTip(
                f"Write every texture in the cache out as {format.label}"
                if everything
                else f"Write the selected textures out as {format.label}"
            )
            triggers(entry, partial(self.exported.emit, format, everything))

        return menu

    def grid_menu(self, parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        menu.addMenu("&Alpha Mode").addActions(list(self._tones.values()))

        return menu

    def context_menu(
        self,
        parent: QWidget,
        selected: int,
        *,
        idle: bool,
        previewing: bool,
    ) -> QMenu:
        menu = QMenu(parent)

        preview = menu.addAction("Hide Preview" if previewing else "Preview")
        preview.setShortcut(QKeySequence(Qt.Key.Key_Space))
        preview.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        preview.setShortcutVisibleInContextMenu(True)

        triggers(preview, self.preview_toggled.emit)

        checkerboard = menu.addMenu("&Alpha Mode")
        checkerboard.addActions(list(self._tones.values()))

        menu.addSeparator()

        copy = menu.addAction("Copy")
        copy.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        copy.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        copy.setShortcutVisibleInContextMenu(True)
        triggers(copy, self.copied.emit)

        # one uuid is all the clipboard can usefully hold
        if selected == 1:
            copy_uuid = menu.addAction("Copy UUID")
            copy_uuid.setShortcut(UUID_KEY)
            copy_uuid.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            copy_uuid.setShortcutVisibleInContextMenu(True)
            triggers(copy_uuid, self.uuid_copied.emit)

        menu.addSeparator()

        entries = self.format_menu(menu, export_title(selected, everything=False), everything=False)
        entries.setEnabled(idle)

        return menu

    def sync_find(self, *, opened: bool, asking: bool) -> None:
        """Nothing can be asked of a cache that is not open, or put down unless it was asked"""

        self.filter_color.setEnabled(opened)
        self.match.setEnabled(opened)
        self.paste.setEnabled(opened)
        self.disable.setEnabled(asking)

        self.cache_order.setText(order_label(Order.CACHE, matching=asking))

    def sync_export(self, selected: int, total: int, *, idle: bool) -> None:
        self.copy.setEnabled(selected > 0)
        self.copy_uuid.setEnabled(selected == 1)

        self._selected_export.setTitle(export_title(selected, everything=False))
        self._selected_export.setEnabled(idle and selected > 0)

        self._all_export.setTitle(export_title(total, everything=True))
        self._all_export.setEnabled(idle and total > 0)

    def populate_recents(self) -> None:
        recents = RecentCaches.shared()
        paths = recents.paths()

        self._recents.clear()
        self._recents.setEnabled(bool(paths))

        for path in paths:
            entry = self._recents.addAction(str(path).replace("&", "&&"))
            entry.setStatusTip(f"Open {path}")
            triggers(entry, partial(self.reopened.emit, path))

        self._recents.addSeparator()

        clear = self._recents.addAction("Clear recent file list")
        clear.setStatusTip("Forget the caches opened before")
        triggers(clear, recents.clear)

    def populate_suggested(self) -> None:
        paths = suggested_paths()

        self._suggested.setEnabled(bool(paths))

        for path in paths:
            entry = self._suggested.addAction(str(path).replace("&", "&&"))
            entry.setStatusTip(f"Open {path}")
            triggers(entry, partial(self.reopened.emit, path))

    @staticmethod
    def store_preview(shown: bool) -> None:
        """Put the preview's tick away without a menu to hand

        The preview belongs to the app rather than to a window, so what it is
        doing is settled somewhere no one WindowActions is in scope.
        """

        store_toggle(PREVIEW_KEY, shown)


class AppMenu:
    def __init__(self, new_window: Callable[[], object], about: Callable[[], object]) -> None:
        self.bar = QMenuBar()

        # the bar owns the entries as well as showing them, since it outlives
        # every window, which is the whole point of it
        self.actions = WindowActions(self.bar, self.bar)

        self._current: WindowActions | None = None

        # the two entries that still mean something with no window to act on
        self._windowless: dict[str, Callable[[], object]] = {"new_window": new_window, "abouted": about}

        for name in FORWARDED:
            asked: SignalInstance = getattr(self.actions, name)
            asked.connect(partial(self.forward, name))

        for key in self.actions.ticks():
            self.actions.tick(key).toggled.connect(partial(self.retick, key))

        # what the export entries are named depends on how much is selected,
        # which the window only works out as its own menu opens
        self.actions.exports.aboutToShow.connect(self.refresh)

        self.follow(None)

    def follow(self, actions: WindowActions | None) -> None:
        self._current = actions

        self.actions.mirror(actions)

    def resync(self, actions: WindowActions) -> None:
        if self._current is actions:
            self.actions.mirror(actions)

    def forget(self, actions: WindowActions) -> None:
        if self._current is actions:
            self.follow(None)

    def refresh(self) -> None:
        current = self._current

        if current is None:
            return

        # the window names its export entries on the way into its own menu,
        # which is the only place the counts behind them are worked out
        current.exports.aboutToShow.emit()

        self.actions.mirror(current)

    def forward(self, name: str, *asked: object) -> None:
        """Hand an entry to the window in front, as it was asked"""

        current = self._current

        if current is not None:
            answers: SignalInstance = getattr(current, name)
            answers.emit(*asked)

            return

        windowless = self._windowless.get(name)

        if windowless is not None:
            windowless()

    def retick(self, key: str, shown: bool) -> None:
        current = self._current

        if current is not None:
            current.tick(key).setChecked(shown)
