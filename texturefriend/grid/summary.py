"""The line the status bar rests on

What a window has to say about itself when nothing has happened lately: how
much of the cache is in the grid, and what is being left out of it.
"""

from texturefriend.grid.model import TextureModel
from texturefriend.view.formatting import format_count


def narrowed_summary(model: TextureModel) -> str:
    shown = format_count(model.rowCount())

    return f"Showing {shown} of {format_count(model.total())} textures matching filters"


def ranked_summary(model: TextureModel, name: str | None) -> str:
    shown = format_count(model.rowCount())

    return f"Showing the {shown} textures most like {name or 'the image'}"


def grid_summary(model: TextureModel, entries: int, *, counting_incomplete: bool) -> str:
    shown = model.rowCount()

    summary = f"Showing {format_count(shown)} textures of {format_count(entries)} entries in cache"

    # both counts are of the rows the grid ended up with rather than of the
    # cache, since an entry left out of it is neither shown nor news
    unfinished = sum(1 for row in range(shown) if not model.texture(row).complete) if counting_incomplete else 0

    if unfinished:
        summary += f", {format_count(unfinished)} incomplete"

    hidden = model.hidden()

    if hidden:
        summary += f", {format_count(hidden)} simple textures hidden"

    return summary


def empty_message(model: TextureModel | None) -> str:
    if model is None or not (model.narrowed or model.hidden()):
        return "Cache is empty"

    return "No textures match filters"
