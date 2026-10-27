import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from math import cbrt, dist, exp, hypot

from PySide6.QtGui import QColor, QImage

type Lab = tuple[float, float, float]

# how many colors are kept for a texture. a thumbnail is a few hundred pixels,
# and past a handful of colors the rest of them are specks
CLUSTERS = 5

# a pixel this faint shows none of its color, whatever the color is. above it
# a pixel counts for as much of itself as it is opaque, so a pane of glass or a
# haze of smoke is read as the little color it puts up rather than as a wall
ALPHA_FLOOR = 32

# how much of a texture has to be opaque before it is judged on its opaque
# pixels alone. under this the score is scaled down to match, so a texture that
# is three visible pixels of red does not outrank one that is red all over
MIN_COVERAGE = 0.1

# the lab bins the pixels are gathered into. a texture's colors are whatever
# survives rounding at this scale, which is a little coarser than the step at
# which anyone would give two colors different names
BIN_L = 12.0
BIN_AB = 16.0

# light and shadow move a texture's lightness much further than they move its
# hue, so a lit and a shaded patch of one paint should read as the one color.
# a color with no hue to go on has only its lightness to be told apart by,
# though, or every grey in a cache answers for black, so the discount is let
# out again as the color asked for loses its chroma
LIGHTNESS_WEIGHT = 0.45
FULL_CHROMA = 60.0

# how much a difference in chroma counts, next to a difference in hue. dust,
# wear and distance wash a color out without moving it round the wheel, the
# same way light and shadow move its lightness, so a faded red is still the red
# that was asked for. the discount fades out as either of the two colors greys,
# for the reason the one above fades: a color with no hue has nothing but its
# chroma to be told apart by, and discounting that hands every grey in the
# cache to whatever was asked for
CHROMA_WEIGHT = 2.0

# how much further apart lab writes a difference at chroma and at hue as a pair
# of colors moves out from grey, from cie's own correction. both are room let
# out, which `tightening` pays back over the query so that letting it out
# changes the shape of a match without changing how far one reaches
CHROMA_TOLERANCE = 0.045
HUE_TOLERANCE = 0.015

# the distance at which a color has fallen to about a third of a match, in the
# weighted lab the rest of this works in
SIGMA = 14.0

# how near two of a texture's own colors have to be before they are the one
# color. a bin edge is an arbitrary place to cut a patch that shades smoothly
# across it, and most textures lose a slot or more to halves that would answer
# alike to anything asked of them, so what putting those back together frees
# goes to colors that really are distinct.
#
# nothing to do with SIGMA, near as the two numbers are: this one says when two
# colors are one color, and that one says when a color answers for another.
# further out than this the halves being joined stop being halves of anything,
# and one centroid ends up standing in for two colors nobody would give the
# same name to
MERGE = 11.0

# how far out a color is still worth working out the falloff for, in sigmas
CUTOFF = 3.0

# how much of a texture has to be near a color before it is worth showing
MATCH_FLOOR = 0.12

# how far apart two of a texture's raw channel levels can stand and still be
# the one color. a thumbnail is decoded out of a lossy codestream, so a texture
# painted one flat color comes back a level or two either side of it
FLAT_RANGE = 4

# the same for opacity. a solid color cut into a shape is a sprite rather than
# a blank, and the shape is in the opacity, so a texture only counts as flat
# when its opacity is as unvarying as its color
FLAT_ALPHA_RANGE = 4

# what a texture with no picture in it is allowed to cost, as the markers every
# codestream opens with plus a little for the pixels themselves. a thumbnail is
# sixteen pixels at the most, which is few enough to average a weave or a grain
# away to nothing, so what the thumbnail says has to be borne out by what the
# encoder needed. the base carries almost all of the bar, since a blank costs
# about what its header costs however large it is. the rate is only there so a
# blank at cache sizes is not damned by the few bytes its size alone adds, and
# it is kept small because a codestream grows nothing like as fast as a pixel
# count does, which at any generous rate hands a large texture room to hide a
# whole picture in
FLAT_BASE_BYTES = 448
FLAT_MAX_DENSITY = 0.001

# how large a texture has to be before the bytes are allowed to overrule a
# thumbnail that shows nothing at all. a thumbnail loses a sprite sheet's few
# opaque marks by averaging them against everything around them, which only
# happens when the texture is very much larger than the sixteen pixels it was
# reduced to. a small texture that comes back empty was empty, whatever it
# cost, and past this the bar above separates the two cleanly on its own
CLEAR_MIN_PIXELS = 1024 * 1024

# what a texture is allowed to cost when its thumbnail is a single pixel. the
# cache keeps a good few of them that way, and one pixel is one color whatever
# it was reduced from, so it is not evidence of anything and the bytes decide
# alone. they can be held much tighter here than where a thumbnail has a say,
# since a texture kept at one pixel is small and a blank one is all header
BLIND_BASE_BYTES = 304

# bits of each channel a color is rounded to before it is counted, which is
# 32768 colors and an error too small to move a texture in the ranking
QUANTUM = 5
LEVELS = 1 << QUANTUM

# the rounding itself, as the byte for byte table `bytes.translate` takes. the
# ends of the range stay where they are, so black and white, which are the two
# colors anyone reaches for first, land exactly on themselves
QUANTIZE = bytes(round((value >> (8 - QUANTUM)) * 255 / (LEVELS - 1)) for value in range(256))

# srgb's transfer curve, which is the only per channel work in the conversion.
# the foot of the curve is a straight line of this slope, and the rest of it is
# the power law above, both as the standard writes them
SRGB_TOE_SLOPE = 12.92
SRGB_TOE_END = 10
SRGB_OFFSET = 0.055
SRGB_GAMMA = 2.4

LINEAR = [
    value / (255 * SRGB_TOE_SLOPE)
    if value <= SRGB_TOE_END
    else ((value / 255 + SRGB_OFFSET) / (1 + SRGB_OFFSET)) ** SRGB_GAMMA
    for value in range(256)
]

# the two constants cielab is cut at, written as cie's own integers rather than
# as the rounded decimals the older printings of the standard carry
CIE_EPSILON_NUMERATOR = 216
CIE_KAPPA_NUMERATOR = 24389
CIE_KAPPA_DENOMINATOR = 27

EPSILON = CIE_EPSILON_NUMERATOR / CIE_KAPPA_NUMERATOR
KAPPA = CIE_KAPPA_NUMERATOR / CIE_KAPPA_DENOMINATOR
WHITE_X = 0.95047
WHITE_Z = 1.08883


@cache
def to_lab(rgb: int) -> Lab:
    """CIELAB for a packed rgb color, which is a space distance means something in

    Kept, since a cache is read one rounded color at a time and the same few
    thousand of them come round again and again.
    """

    red = LINEAR[(rgb >> 16) & 0xFF]
    green = LINEAR[(rgb >> 8) & 0xFF]
    blue = LINEAR[rgb & 0xFF]

    x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / WHITE_X
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = (0.0193339 * red + 0.1191920 * green + 0.9503041 * blue) / WHITE_Z

    fx = cbrt(x) if x > EPSILON else (KAPPA * x + 16) / 116
    fy = cbrt(y) if y > EPSILON else (KAPPA * y + 16) / 116
    fz = cbrt(z) if z > EPSILON else (KAPPA * z + 16) / 116

    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


@cache
def binned(rgb: int) -> tuple[Lab, tuple[int, int, int]]:
    """A rounded color in lab, beside the bin it is gathered into

    Rounding to a bin is a cluster of the only kind a few hundred pixels can
    support, and unlike a k-means it lands in the same place each run.
    """

    lightness, green_red, blue_yellow = lab = to_lab(rgb)

    return lab, (round(lightness / BIN_L), round(green_red / BIN_AB), round(blue_yellow / BIN_AB))


def lab_of(color: QColor) -> Lab:
    return to_lab((color.red() << 16) | (color.green() << 8) | color.blue())


def hueness(chroma: float) -> float:
    return min(chroma, FULL_CHROMA) / FULL_CHROMA


def lightness_weight(target: Lab) -> float:
    return 1.0 - (1.0 - LIGHTNESS_WEIGHT) * hueness(hypot(target[1], target[2]))


def tightening(target_chroma: float) -> float:
    room = (
        (1.0 + (CHROMA_WEIGHT - 1.0) * hueness(target_chroma))
        * (1.0 + CHROMA_TOLERANCE * target_chroma)
        * (1.0 + HUE_TOLERANCE * target_chroma)
    )

    return cbrt(room * room)


# where opacity sits among the four bytes of a word of argb, which lies in
# memory whichever way round the machine keeps its words
ALPHA_BYTE = 3 if sys.byteorder == "little" else 0


def packed(image: QImage) -> bytes:
    """The image's pixels, as they were decoded"""

    argb = image.convertToFormat(QImage.Format.Format_ARGB32)

    # a row of 32 bit pixels is never padded out, so the buffer holds the
    # pixels and nothing besides, in the order a word of argb reads back in
    return bytes(argb.constBits())


def counted(raw: bytes) -> Counter[int]:
    """How many pixels the image holds of each color, at the levels lab is kept against"""

    return Counter(memoryview(raw.translate(QUANTIZE)).cast("I"))


def spans(raw: bytes) -> tuple[int, int]:
    """How far the colors and the opacities in an image stand apart, in raw levels

    Off the pixels as they were decoded rather than the rounded ones the color
    index counts. Rounding drops two neighbouring levels in different bins as
    readily as in the same one, and the grey a normal map is mostly made of
    sits on a bin edge exactly, so a texture painted one flat color comes back
    from the rounding looking like a texture painted two.
    """

    channels = [raw[at::4] for at in range(4)]
    opacities = channels.pop(ALPHA_BYTE)

    return max(max(levels) - min(levels) for levels in channels), max(opacities) - min(opacities)


@dataclass(frozen=True)
class Signature:
    colors: list[tuple[Lab, float]] = field(default_factory=list)

    flat: bool = False
    clear: bool = False


def signature(image: QImage) -> Signature | None:
    if image.isNull():
        return None

    raw = packed(image)

    if not raw:
        return None

    counts = counted(raw)
    gathered: dict[tuple[int, int, int], list[float]] = {}

    total = 0
    shown = 0.0

    for pixel, count in counts.items():
        total += count

        alpha = pixel >> 24

        if alpha < ALPHA_FLOOR:
            continue

        weight = count * (alpha / 255)
        shown += weight

        (lightness, green_red, blue_yellow), key = binned(pixel & 0xFFFFFF)
        bin = gathered.get(key)

        if bin is None:
            gathered[key] = [weight, lightness * weight, green_red * weight, blue_yellow * weight]
        else:
            bin[0] += weight
            bin[1] += lightness * weight
            bin[2] += green_red * weight
            bin[3] += blue_yellow * weight

    if not shown:
        return Signature(flat=True, clear=True)

    coverage = min(1.0, shown / (total * MIN_COVERAGE))

    top = sorted(merged(gathered.values()), key=lambda bin: bin[0], reverse=True)[:CLUSTERS]

    colors = [
        ((lightness, green_red, blue_yellow), weight / shown * coverage)
        for weight, lightness, green_red, blue_yellow in top
    ]

    color, opacity = spans(raw)

    # the thumbnail is only half of it: the cache keeps some of them at a
    # single pixel, which is one solid color whatever it was reduced from, so
    # what the bytes say is what settles those
    return Signature(colors, flat=color <= FLAT_RANGE and opacity <= FLAT_ALPHA_RANGE)


def merged(bins: Iterable[list[float]]) -> list[list[float]]:
    """The bins as their centroids, with any that are the one color put back together

    Working down from the heaviest bin, since that is the one a split has left
    most of the color in and so the one the rest of it should be gathered onto.
    """

    centroids = sorted(
        ([weight, sum_l / weight, sum_a / weight, sum_b / weight] for weight, sum_l, sum_a, sum_b in bins),
        key=lambda bin: bin[0],
        reverse=True,
    )

    kept: list[list[float]] = []
    points: list[Lab] = []

    for weight, lightness, green_red, blue_yellow in centroids:
        point = (lightness, green_red, blue_yellow)

        for at, into in enumerate(kept):
            if dist(point, points[at]) >= MERGE:
                continue

            # where one bin would have put the centroid, had the edge not
            # fallen across the patch
            heavier = into[0]
            into[0] = heavier + weight
            into[1] = (into[1] * heavier + lightness * weight) / into[0]
            into[2] = (into[2] * heavier + green_red * weight) / into[0]
            into[3] = (into[3] * heavier + blue_yellow * weight) / into[0]
            points[at] = (into[1], into[2], into[3])

            break
        else:
            kept.append([weight, lightness, green_red, blue_yellow])
            points.append(point)

    return kept


class ColorIndex:
    def __init__(self, count: int) -> None:
        self._count = count

        self._rows: list[int] = []
        self._lightness: list[float] = []
        self._green_red: list[float] = []
        self._blue_yellow: list[float] = []
        self._chroma: list[float] = []
        self._weights: list[float] = []
        self._flat: set[int] = set()

    def __len__(self) -> int:
        return self._count

    @property
    def flat(self) -> set[int]:
        """The rows holding one solid color, or nothing visible at all"""

        return self._flat

    def add(self, row: int, signature: Signature) -> None:
        if signature.flat:
            self._flat.add(row)

        for (lightness, green_red, blue_yellow), weight in signature.colors:
            self._rows.append(row)
            self._lightness.append(lightness)
            self._green_red.append(green_red)
            self._blue_yellow.append(blue_yellow)

            # scoring reads this off every entry it walks, once for each
            # color it is asked about, so it is worked out the once here
            self._chroma.append(hypot(green_red, blue_yellow))

            self._weights.append(weight)

    def scores(self, colors: Sequence[QColor]) -> list[float]:
        """How much of each texture is near every one of the colors asked for

        A texture is scored by the color it holds the least of, so the ones
        that answer for the whole set come out ahead of the ones that only
        answer for part of it.
        """

        # nothing asked for is a filter nothing fails, so every texture is a
        # full match and the loop below has nothing to narrow it with
        scored = [1.0] * self._count

        for color in colors:
            scored = list(map(min, scored, self.matches(color)))

        return scored

    def matches(self, color: QColor) -> list[float]:
        """How much of each texture is near the one color"""

        target_l, target_a, target_b = lab_of(color)
        target_chroma = hypot(target_a, target_b)

        lightness_falls = lightness_weight((target_l, target_a, target_b))
        paid_back = tightening(target_chroma)
        discount = CHROMA_WEIGHT - 1.0

        falloff = 1.0 / (SIGMA * SIGMA)
        cutoff = (CUTOFF * SIGMA) ** 2

        match = [0.0] * self._count

        # the lists come out of self up front, since the loop below runs once
        # for every color every texture in the cache shows
        rows = self._rows
        lightness = self._lightness
        green_red = self._green_red
        blue_yellow = self._blue_yellow
        chroma = self._chroma
        weights = self._weights

        for entry, row in enumerate(rows):
            entry_chroma = chroma[entry]

            delta_l = (lightness[entry] - target_l) * lightness_falls
            delta_a = green_red[entry] - target_a
            delta_b = blue_yellow[entry] - target_b

            # what is left of the ab difference once the part of it that runs
            # along chroma is taken out is the part that runs round the wheel,
            # which is the difference in hue and the one that is kept whole
            delta_chroma = entry_chroma - target_chroma
            delta_hue = max(delta_a * delta_a + delta_b * delta_b - delta_chroma * delta_chroma, 0.0)

            # the chroma discount goes only as far as the greyer of the two
            # has hue to trade: a color with no chroma has no hue either, so
            # the whole of its difference lands here, and discounting that
            # would hand every grey in the cache to whatever was asked for
            mean_chroma = (entry_chroma + target_chroma) * 0.5
            shared = 1.0 + discount * hueness(min(entry_chroma, target_chroma))

            at_chroma = delta_chroma / (shared * (1.0 + CHROMA_TOLERANCE * mean_chroma))
            at_hue = 1.0 + HUE_TOLERANCE * mean_chroma

            distance = paid_back * (delta_l * delta_l + at_chroma * at_chroma + delta_hue / (at_hue * at_hue))

            # a color this far off counts for nothing either way, and asking
            # costs far less than working out how little
            if distance < cutoff:
                match[row] += weights[entry] * exp(-distance * falloff)

        return match
