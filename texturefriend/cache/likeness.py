"""Which texture a picture is of

A cache is searched by handing it a picture — a screenshot of a texture in
world, or a copy of the texture off disk — and asking which of its entries
look like it. All an entry has to answer with is the thumbnail the viewer kept
beside it, which is sixteen pixels square at the most, so both sides are
squashed down to the coarse grid they can meet on and compared there, in the
same lab the colour filters work in.

What a picture of a texture keeps of the texture is where its light and dark
patches lie, and what it loses is everything else: a screenshot is lit by
whatever was around it, taken at whatever size the camera was, and cropped by
hand. So the comparison is made against the layout alone, with each side's own
brightness and contrast divided out of it first, and how light or how coloured
the two are overall left to settle the ties.
"""

import math
from array import array
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

from texturefriend.cache.color import QUANTIZE, Lab, to_lab

# cells across the squash both sides are compared at. a thumbnail is sixteen
# pixels square at the most, and half of that is as much as a picture taken
# through a viewer's camera can be trusted to line up to
GRID = 8
CELLS = GRID * GRID

# what the clear parts of both sides are laid over. a screenshot shows whatever
# was behind the texture and a thumbnail shows whatever the encoder happened to
# leave in its clear pixels, so neither is worth comparing on its own account;
# laying both over the one grey at least has them meet somewhere
BACKDROP = 0x80

# how much where a texture's colour lies counts next to where its light and
# dark lie. lightness carries a picture, which is why photographs read in
# black and white, and the colour layout is the second opinion
CHROMA_WEIGHT = 0.3

# the same for the shape cut out of a texture by its opacity. a sprite is
# mostly its outline, but a screenshot of one has whatever was behind it in
# place of the clear pixels, so this is a hint rather than a demand
OPACITY_WEIGHT = 0.3

# how much of a match a lab unit of difference in overall colour costs, and the
# same for overall lightness. a picture taken in world is lit by whatever was
# around it, so how light the whole of it came out says little and is charged
# for accordingly; its colour survives the lighting rather better
COLOR_PENALTY = 0.01
LIGHTNESS_PENALTY = 0.005

# how faint a layout can be before it is noise rather than a picture. a cosine
# between two of those is meaningless, so the weaker side's reach is what the
# comparison is discounted by
FAINTEST = 1e-6

# the opacity of a cell, on the scale the lab channels are kept at, so that one
# weight covers all of them
OPAQUE = 100.0


def squashed(image: QImage) -> list[tuple[float, float, float, float]]:
    """The image as one lab colour and one opacity per cell of a coarse grid

    Aspect ratio is not kept: a texture is worn on whatever shape the thing
    wearing it happens to be, and the thumbnail beside it in the cache has
    already been squashed once by the viewer.
    """

    scaled = image.convertToFormat(QImage.Format.Format_ARGB32).scaled(
        QSize(GRID, GRID),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    cells = []

    # a row of 32 bit pixels is never padded, so the buffer is the pixels and
    # nothing besides, in the order a word of argb reads back in
    for pixel in memoryview(bytes(scaled.constBits())).cast("I"):
        alpha = pixel >> 24

        if alpha == 0xFF:
            lightness, green_red, blue_yellow = to_lab(rounded(pixel & 0xFFFFFF))
        else:
            lightness, green_red, blue_yellow = to_lab(rounded(over_backdrop(pixel, alpha)))

        cells.append((lightness, green_red, blue_yellow, alpha * OPAQUE / 255))

    return cells


def rounded(rgb: int) -> int:
    """The colour on the grid `to_lab` keeps its table against

    Every other caller hands it a colour already rounded, which is what holds
    the table to the few thousand entries it was written for. A cell of a
    squash is an average and lands anywhere, so a cache read whole would leave
    one entry per cell per texture behind in a table nothing ever empties.
    """

    return (QUANTIZE[rgb >> 16] << 16) | (QUANTIZE[(rgb >> 8) & 0xFF] << 8) | QUANTIZE[rgb & 0xFF]


def over_backdrop(pixel: int, alpha: int) -> int:
    """The pixel as it looks laid over the grey, packed the way `to_lab` reads one"""

    shown = alpha / 255
    hidden = BACKDROP * (1 - shown)

    return sum(round(((pixel >> shift) & 0xFF) * shown + hidden) << shift for shift in (16, 8, 0))


def centred(values: list[float]) -> list[float]:
    """The channel with how high it sits taken out of it, leaving only where it moves

    This is what lets a texture answer for a picture of itself taken under
    other lighting, or in another paint.
    """

    mean = sum(values) / len(values)

    return [value - mean for value in values]


def scaled(deviation: list[float]) -> tuple[tuple[float, ...], float]:
    """The layout at unit length, with how far it reached before that beside it

    Scaling it is what lets a texture answer for a picture of itself taken
    through a duller lens. How far it reached is kept because a layout scaled
    up out of nothing is noise, and has to be discounted as such.
    """

    reach = math.sqrt(math.sumprod(deviation, deviation))

    if reach < FAINTEST:
        return (0.0,) * len(deviation), 0.0

    return tuple(value / reach for value in deviation), reach


def layout(values: list[float]) -> tuple[tuple[float, ...], float]:
    """Where a channel is high and where it is low, without how high or how low"""

    return scaled(centred(values))


@dataclass(frozen=True, slots=True)
class Descriptor:
    """What one picture is remembered by, once it is too small to be a picture"""

    # where the light and dark patches lie
    light: array[float]

    # where the colour lies, as the two lab channels laid end to end and scaled
    # together, so that a texture with all its colour in one of them is not
    # halved for having none in the other
    chroma: array[float]

    # where the texture is solid and where it is clear
    opacity: array[float]

    # how far each of the three reached before it was scaled to unit length
    contrast: float
    colourfulness: float
    shapeliness: float

    # the whole picture as one colour, which is what tells two textures of the
    # same layout apart
    mean: Lab


def describe(image: QImage) -> Descriptor | None:
    """What to remember an image by, or nothing if there is no image"""

    if image.isNull():
        return None

    cells = squashed(image)

    light, contrast = layout([cell[0] for cell in cells])
    opacity, shapeliness = layout([cell[3] for cell in cells])

    # the two colour channels are centred one at a time and only then laid end
    # to end, since a texture painted one flat colour sits high in one of them
    # and low in the other, and centring the pair together would read that as
    # a layout
    chroma, colourfulness = scaled(centred([cell[1] for cell in cells]) + centred([cell[2] for cell in cells]))

    return Descriptor(
        light=array("f", light),
        chroma=array("f", chroma),
        opacity=array("f", opacity),
        contrast=contrast,
        colourfulness=colourfulness,
        shapeliness=shapeliness,
        mean=(
            sum(cell[0] for cell in cells) / CELLS,
            sum(cell[1] for cell in cells) / CELLS,
            sum(cell[2] for cell in cells) / CELLS,
        ),
    )


def shared(one: float, other: float) -> float:
    """How much of a layout the fainter of two has to compare with the other's

    Two textures with colour in them can be told apart by where it lies; a grey
    one has only the rounding of its own greys to answer with, and letting that
    stand in for a colour layout hands every grey in a cache to whatever is
    asked of it.
    """

    return min(one, other) / max(one, other) if max(one, other) > FAINTEST else 0.0


def likeness(query: Descriptor, entry: Descriptor) -> float:
    """How much the two look alike, as a number only worth comparing to another of these

    One for two copies of the same layout, a little more than that once the
    colour and the outline agree as well, nothing much for two pictures with no
    more in common than any two picked at random, and under nothing for two
    that disagree.

    It is signed rather than floored because the two are only ever ranked
    against each other. Flooring it would read a texture laid out exactly like
    the picture but painted the complementary colour as no answer at all,
    rather than as the poor one it is, and drop it out of the ranking on the
    strength of a penalty meant only to settle ties.
    """

    colour = CHROMA_WEIGHT * shared(query.colourfulness, entry.colourfulness)
    shape = OPACITY_WEIGHT * shared(query.shapeliness, entry.shapeliness)

    return (
        math.sumprod(query.light, entry.light)
        + colour * math.sumprod(query.chroma, entry.chroma)
        + shape * math.sumprod(query.opacity, entry.opacity)
        - COLOR_PENALTY * math.dist(query.mean[1:], entry.mean[1:])
        - LIGHTNESS_PENALTY * abs(query.mean[0] - entry.mean[0])
    )


class LikenessIndex:
    """What every texture in a cache looks like, ready to be asked about a picture"""

    def __init__(self, count: int) -> None:
        self._count = count
        self._entries: dict[int, Descriptor] = {}

    def __len__(self) -> int:
        return self._count

    def add(self, row: int, descriptor: Descriptor) -> None:
        self._entries[row] = descriptor

    def scores(self, query: Descriptor) -> dict[int, float]:
        """How much each texture looks like the picture, for the rows there is one for

        A texture the scan could not read a thumbnail for is left out rather
        than scored nothing: nothing is a score a read texture can earn, and
        the two are not the same answer.
        """

        return {row: likeness(query, entry) for row, entry in self._entries.items()}
