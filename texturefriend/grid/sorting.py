from enum import StrEnum

from PySide6.QtCore import QSettings
from texture_courier import Dimensions, Texture, TextureCacheError

from texturefriend.settings import SettingsWatcher

ORDER = "sortOrder"
DESC = "sortDescending"


class Order(StrEnum):
    CACHE = "cache"
    DIMENSIONS = "dimensions"
    DISK_SIZE = "diskSize"


ORDER_LABELS = {
    Order.CACHE: "By Cache Order",
    Order.DIMENSIONS: "By Dimensions",
    Order.DISK_SIZE: "By Disk Size",
}

# when a colour or a picture ranks the grid
MATCH_LABEL = "By Best Match"


class SortChanges(SettingsWatcher):
    pass


def order_label(order: Order, *, matching: bool) -> str:
    if order is Order.CACHE and matching:
        return MATCH_LABEL

    return ORDER_LABELS[order]


def sort_order() -> Order:
    stored = QSettings().value(ORDER)

    return next((order for order in Order if order.value == stored), Order.CACHE)


def set_sort_order(order: Order) -> None:
    if order == sort_order():
        return

    QSettings().setValue(ORDER, order.value)

    SortChanges.shared().changed.emit()


def sort_descending() -> bool:
    return bool(QSettings().value(DESC, True, type=bool))


def set_sort_descending(descending: bool) -> None:
    if descending == sort_descending():
        return

    QSettings().setValue(DESC, descending)

    SortChanges.shared().changed.emit()


def dimensions(texture: Texture) -> Dimensions | None:
    try:
        found = texture.dimensions()
    except (TextureCacheError, OSError):
        return None

    if found is None or not found.width or not found.height:
        return None

    return found


def sort_key(texture: Texture, order: Order) -> tuple[int, ...] | None:
    if order is Order.DISK_SIZE:
        return None if texture.empty else (texture.cached_size,)

    found = dimensions(texture)

    return None if found is None else (found.width * found.height, found.width)
