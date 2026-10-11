"""Which textures the grid is showing, and in what order

A cache is filtered by colour or ranked against a picture, and either way the
textures holding no picture at all can be left out of it. All three answers
come out of the scan, which arrives long after the grid has been filled, so
what is being asked for is kept here and applied whenever there is something
to apply it against.
"""

from dataclasses import dataclass, field

from PySide6.QtGui import QColor

from texturefriend.cache.color import MATCH_FLOOR
from texturefriend.cache.likeness import Descriptor
from texturefriend.cache.scan import Scan

SHORTLIST = 200


@dataclass
class Narrowing:
    colors: list[QColor] = field(default_factory=list)

    # the picture the cache is being searched for, as the little of it that
    # survives being squashed down to the size a thumbnail can answer at
    reference: Descriptor | None = None

    # whether the textures holding one solid colour, or nothing visible at all,
    # are being left out of the grid rather than only ringed in it
    simple_hidden: bool = False

    incomplete: frozenset[int] = frozenset()
    incomplete_hidden: bool = False

    # what the scan found, which is nothing until it lands
    scan: Scan | None = None

    @property
    def asking(self) -> bool:
        return self.searching or self.simple_hidden

    @property
    def searching(self) -> bool:
        return bool(self.colors) or self.reference is not None

    @property
    def waiting(self) -> bool:
        if self.scan is None:
            return self.asking

        return self.searching and not self.incomplete_hidden and not self.scan.everything

    @property
    def narrowed(self) -> bool:
        return self.searching and self.scan is not None

    @property
    def matching(self) -> bool:
        return self.reference is not None and self.scan is not None

    def hidden(self) -> int:
        if not self.simple_hidden or self.scan is None:
            return 0

        return len(self.scan.colors.flat)

    def total(self, count: int) -> int:
        return count - len(self.incomplete) if self.incomplete_hidden else count

    def candidates(self, count: int) -> list[int]:
        if not self.incomplete_hidden:
            return list(range(count))

        return [row for row in range(count) if row not in self.incomplete]

    def flat_uuids(self, uuids: list[str]) -> set[str]:
        if self.scan is None:
            return set()

        return {uuids[row] for row in self.scan.colors.flat}

    def shown_rows(self, count: int) -> list[int] | None:
        """Nothing at all when something is being asked for that only the scan can
        answer and the scan has not landed: the caller leaves the grid as it is
        and this is asked again when it does.
        """

        rows = self.candidates(count)

        if not self.asking:
            return rows

        if self.waiting or self.scan is None:
            return None

        flat = self.scan.colors.flat if self.simple_hidden else set()

        kept = [row for row in rows if row not in flat]

        # a picture is asked instead of the colours rather than inside them.
        # what a texture holds and what it looks like are different questions,
        # and taking either of them up on the bar puts the other one down
        if self.reference is not None:
            return self.by_likeness(kept)

        if self.colors:
            return self.by_color(kept)

        return kept

    def by_color(self, rows: list[int]) -> list[int]:
        scan = self.scan

        if scan is None:
            return rows

        scores = scan.colors.scores(self.colors)

        # a texture has to hold some of every colour asked for, and the more
        # colours are asked for the less of each one any texture can be
        floor = MATCH_FLOOR / len(self.colors)

        kept = [row for row in rows if scores[row] >= floor]
        kept.sort(key=lambda row: -scores[row])

        return kept

    def by_likeness(self, rows: list[int]) -> list[int]:
        scan = self.scan

        if scan is None or self.reference is None:
            return rows

        scores = scan.likeness.scores(self.reference)

        # every texture the scan could read is ranked, and where to cut the
        # ranking is the one question the scores cannot answer: they are worth
        # comparing to each other and to nothing else. so it is held to a count
        kept = [row for row in rows if row in scores]
        kept.sort(key=lambda row: -scores[row])

        return kept[:SHORTLIST]
