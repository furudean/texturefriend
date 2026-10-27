"""Colour signatures and the index that answers queries against them"""

from array import array

from PySide6.QtGui import QColor, QImage

from texturefriend.cache.color import (
    CLUSTERS,
    FULL_CHROMA,
    LIGHTNESS_WEIGHT,
    ColorIndex,
    Signature,
    counted,
    hueness,
    lab_of,
    lightness_weight,
    merged,
    packed,
    signature,
    spans,
    tightening,
    to_lab,
)


def filled(color: QColor, width: int = 8, height: int = 8) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)

    return image


def signed(image: QImage) -> Signature:
    """The signature of an image that is meant to have one"""

    found = signature(image)

    assert found is not None

    return found


def halved(left: QColor, right: QColor, width: int = 8, height: int = 8) -> QImage:
    image = filled(left, width, height)

    for x in range(width // 2, width):
        for y in range(height):
            image.setPixelColor(x, y, right)

    return image


class TestToLab:
    def test_white_is_fully_light_and_has_no_hue(self) -> None:
        lightness, green_red, blue_yellow = to_lab(0xFFFFFF)

        assert abs(lightness - 100.0) < 0.01
        assert abs(green_red) < 0.01
        assert abs(blue_yellow) < 0.01

    def test_black_is_the_origin(self) -> None:
        assert to_lab(0x000000) == (0.0, 0.0, 0.0)

    def test_grey_keeps_its_lightness_and_loses_its_hue(self) -> None:
        lightness, green_red, blue_yellow = to_lab(0x808080)

        assert 50 < lightness < 60
        assert abs(green_red) < 0.01
        assert abs(blue_yellow) < 0.01

    def test_red_leans_towards_green_red_and_away_from_blue(self) -> None:
        _, green_red, blue_yellow = to_lab(0xFF0000)

        assert green_red > 60
        assert blue_yellow > 40

    def test_blue_leans_the_other_way(self) -> None:
        _, _, blue_yellow = to_lab(0x0000FF)

        assert blue_yellow < -100

    def test_lab_of_a_qcolor_matches_its_packed_value(self, app: object) -> None:
        assert lab_of(QColor(0x20, 0x40, 0x60)) == to_lab(0x204060)


class TestWeighting:
    def test_a_grey_has_no_hueness(self) -> None:
        assert hueness(0.0) == 0.0

    def test_hueness_saturates_at_full_chroma(self) -> None:
        assert hueness(FULL_CHROMA) == 1.0
        assert hueness(FULL_CHROMA * 10) == 1.0

    def test_a_grey_target_is_judged_on_lightness_alone(self) -> None:
        # nothing but lightness tells two greys apart, so none of it is discounted
        assert lightness_weight((50.0, 0.0, 0.0)) == 1.0

    def test_a_saturated_target_discounts_lightness(self) -> None:
        assert abs(lightness_weight((50.0, 80.0, 0.0)) - LIGHTNESS_WEIGHT) < 1e-9

    def test_tightening_pays_back_more_the_further_from_grey(self) -> None:
        assert tightening(0.0) < tightening(30.0) < tightening(80.0)

    def test_a_grey_target_is_given_no_extra_room(self) -> None:
        assert tightening(0.0) == 1.0


class TestPixelReading:
    def test_packed_returns_four_bytes_a_pixel(self, app: object) -> None:
        assert len(packed(filled(QColor(0xFF, 0x00, 0x00), 4, 4))) == 4 * 4 * 4

    def test_counted_gathers_a_flat_image_into_one_colour(self, app: object) -> None:
        counts = counted(packed(filled(QColor(0x11, 0x22, 0x33), 4, 4)))

        assert len(counts) == 1
        assert sum(counts.values()) == 16

    def test_counted_keeps_two_distinct_colours_apart(self, app: object) -> None:
        counts = counted(packed(halved(QColor("red"), QColor("blue"), 4, 4)))

        assert len(counts) == 2
        assert sorted(counts.values()) == [8, 8]

    def test_spans_of_a_flat_image_are_zero(self, app: object) -> None:
        assert spans(packed(filled(QColor(0x40, 0x40, 0x40)))) == (0, 0)

    def test_spans_report_the_widest_channel(self, app: object) -> None:
        color, opacity = spans(packed(halved(QColor(0, 0, 0), QColor(200, 10, 10))))

        assert color == 200
        assert opacity == 0

    def test_spans_report_how_far_the_opacities_stand_apart(self, app: object) -> None:
        raw = array("I", [0xFFFF0000, 0x40FF0000]).tobytes()

        assert spans(raw) == (0, 0xFF - 0x40)


class TestSignature:
    def test_a_null_image_has_no_signature(self, app: object) -> None:
        assert signature(QImage()) is None

    def test_a_flat_image_is_flagged_flat(self, app: object) -> None:
        assert signed(filled(QColor("red"))).flat is True

    def test_a_two_colour_image_is_not_flat(self, app: object) -> None:
        assert signed(halved(QColor("red"), QColor("blue"))).flat is False

    def test_a_flat_image_reports_the_colour_it_holds(self, app: object) -> None:
        found = signed(filled(QColor(0xC0, 0x39, 0x2B)))

        assert len(found.colors) == 1

        (lab, weight) = found.colors[0]

        assert weight == 1.0
        # the quantisation moves the colour a little, but not out of its own neighbourhood
        assert abs(lab[0] - lab_of(QColor(0xC0, 0x39, 0x2B))[0]) < 5

    def test_an_image_with_no_opacity_anywhere_is_clear(self, app: object) -> None:
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(QColor(0xFF, 0x00, 0x00, 0x00))

        # the red is there in the pixels, but nobody can see it, so the texture
        # is filed as showing nothing rather than as showing red
        found = signed(image)

        assert found.clear is True
        assert found.flat is True
        assert found.colors == []

    def test_an_image_too_faint_to_see_is_clear(self, app: object) -> None:
        found = signed(filled(QColor(0xFF, 0x00, 0x00, 0x10)))

        assert found.clear is True

    def test_a_texture_anyone_can_see_is_not_clear(self, app: object) -> None:
        assert signed(filled(QColor("red"))).clear is False

    def test_a_solid_colour_cut_into_a_shape_is_not_flat(self, app: object) -> None:
        # the color is the one color all over and the picture is in the
        # opacity, which makes it a sprite rather than a blank
        image = filled(QColor(0xFF, 0x00, 0x00, 0xFF))

        for y in range(8):
            image.setPixelColor(0, y, QColor(0xFF, 0x00, 0x00, 0x00))

        assert signed(image).flat is False

    def test_a_clear_patch_in_a_lit_image_shows_no_colour(self, app: object) -> None:
        image = filled(QColor(0x00, 0xFF, 0x00, 0xFF), 4, 4)

        for y in range(4):
            image.setPixelColor(0, y, QColor(0xFF, 0x00, 0x00, 0x00))

        # something in it carries opacity, so the clear pixels are believed and
        # the red they are painted is left out of the colours reported
        found = signed(image)

        assert all(green_red < 0.0 for (_, green_red, _), _ in found.colors)

    def test_weights_are_shares_that_sum_to_one(self, app: object) -> None:
        found = signed(halved(QColor("red"), QColor("blue")))

        assert abs(sum(weight for _, weight in found.colors) - 1.0) < 1e-6

    def test_no_more_than_the_cluster_limit_of_colours_is_kept(self, app: object) -> None:
        image = QImage(16, 16, QImage.Format.Format_ARGB32)

        for x in range(16):
            for y in range(16):
                image.setPixelColor(x, y, QColor(x * 16, y * 16, (x ^ y) * 16))

        assert len(signed(image).colors) <= CLUSTERS

    def test_the_heaviest_colour_comes_first(self, app: object) -> None:
        image = filled(QColor("blue"), 8, 8)

        for y in range(8):
            image.setPixelColor(0, y, QColor("red"))

        colors = signed(image).colors

        assert colors[0][1] > colors[-1][1]


class TestMerged:
    def test_bins_far_apart_are_left_alone(self) -> None:
        # a bin arrives as its weight and its weighted sums, not as its centroid
        bins = [[1.0, 0.0, 0.0, 0.0], [1.0, 90.0, 0.0, 0.0]]

        assert len(merged(bins)) == 2

    def test_neighbouring_bins_are_put_back_together(self) -> None:
        bins = [[3.0, 150.0, 0.0, 0.0], [1.0, 52.0, 0.0, 0.0]]
        kept = merged(bins)

        assert len(kept) == 1
        assert kept[0][0] == 4.0
        # the merged centroid is pulled towards the heavier of the two
        assert 50.0 < kept[0][1] < 51.0

    def test_merging_conserves_the_weight_it_started_with(self) -> None:
        bins = [[3.0, 150.0, 0.0, 0.0], [1.0, 52.0, 0.0, 0.0], [2.0, 180.0, 100.0, 0.0]]

        assert sum(bin[0] for bin in merged(bins)) == 6.0

    def test_an_empty_set_of_bins_merges_to_nothing(self) -> None:
        assert merged([]) == []


class TestColorIndex:
    def test_an_index_has_a_row_for_every_texture(self) -> None:
        assert len(ColorIndex(7)) == 7

    def test_with_nothing_asked_for_everything_answers(self, app: object) -> None:
        index = ColorIndex(3)

        assert index.scores([]) == [1.0, 1.0, 1.0]

    def test_flat_rows_are_collected(self, app: object) -> None:
        index = ColorIndex(2)
        index.add(0, Signature(flat=True))
        index.add(1, Signature(colors=[(to_lab(0xFF0000), 1.0)]))

        assert index.flat == {0}

    def test_a_texture_answers_for_the_colour_it_holds(self, app: object) -> None:
        index = ColorIndex(2)
        index.add(0, Signature(colors=[(to_lab(0xFF0000), 1.0)]))
        index.add(1, Signature(colors=[(to_lab(0x0000FF), 1.0)]))

        red, blue = index.matches(QColor("red"))

        assert red > blue

    def test_a_row_with_nothing_indexed_answers_for_nothing(self, app: object) -> None:
        index = ColorIndex(2)
        index.add(0, Signature(colors=[(to_lab(0xFF0000), 1.0)]))

        assert index.matches(QColor("red"))[1] == 0.0

    def test_two_colours_are_scored_by_the_weaker_of_them(self, app: object) -> None:
        index = ColorIndex(1)
        index.add(0, Signature(colors=[(to_lab(0xFF0000), 1.0)]))

        both = index.scores([QColor("red"), QColor("blue")])
        red_only = index.scores([QColor("red")])

        assert both[0] < red_only[0]

    def test_a_texture_holding_both_answers_for_both(self, app: object) -> None:
        index = ColorIndex(1)
        index.add(0, Signature(colors=[(to_lab(0xFF0000), 0.5), (to_lab(0x0000FF), 0.5)]))

        assert index.scores([QColor("red"), QColor("blue")])[0] > 0.0

    def test_scores_never_run_past_a_full_match(self, app: object) -> None:
        index = ColorIndex(1)
        index.add(0, Signature(colors=[(to_lab(0xFF0000), 1.0)]))

        assert index.scores([QColor("red")])[0] <= 1.0
