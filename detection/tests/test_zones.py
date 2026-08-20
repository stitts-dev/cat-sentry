from catsentry.zones import (
    PROTECTED_ZONE,
    REQUIRED_ZONES,
    ZoneMap,
    bbox_bottom_center,
    point_in_polygon,
)

SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]

# An "L" occupying the 2x2 square minus its top-right 1x1 quadrant (a
# reflex/concave corner at (1, 1)).
L_SHAPE = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)]


def test_point_clearly_inside_and_outside_convex_square():
    assert point_in_polygon((1.0, 1.0), SQUARE) is True
    assert point_in_polygon((3.0, 3.0), SQUARE) is False
    assert point_in_polygon((-0.5, 1.0), SQUARE) is False


def test_point_on_vertex_counts_as_inside():
    assert point_in_polygon((0.0, 0.0), SQUARE) is True
    assert point_in_polygon((2.0, 2.0), SQUARE) is True


def test_point_on_edge_counts_as_inside():
    assert point_in_polygon((1.0, 0.0), SQUARE) is True  # mid-point of bottom edge
    assert point_in_polygon((2.0, 1.5), SQUARE) is True  # mid-point of right edge


def test_point_just_outside_edge_is_outside():
    assert point_in_polygon((1.0, -0.001), SQUARE) is False
    assert point_in_polygon((2.001, 1.0), SQUARE) is False


def test_concave_polygon_notch_is_outside():
    # (1.5, 1.5) sits in the missing top-right quadrant of the L.
    assert point_in_polygon((1.5, 1.5), L_SHAPE) is False


def test_concave_polygon_solid_regions_are_inside():
    assert point_in_polygon((0.5, 0.5), L_SHAPE) is True  # bottom-left
    assert point_in_polygon((1.5, 0.5), L_SHAPE) is True  # bottom-right leg
    assert point_in_polygon((0.5, 1.5), L_SHAPE) is True  # top-left leg


def test_concave_polygon_reflex_vertex_counts_as_inside():
    assert point_in_polygon((1.0, 1.0), L_SHAPE) is True


def test_bbox_bottom_center_is_feet_not_centroid():
    assert bbox_bottom_center((0.4, 0.5, 0.2, 0.3)) == (0.5, 0.8)


def test_zone_map_locate_matches_named_zone():
    zones = {
        "floor_left": [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)],
        "floor_right": [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)],
    }
    zone_map = ZoneMap(zones)

    assert zone_map.locate((0.1, 0.1, 0.05, 0.05)) == "floor_left"
    assert zone_map.locate((0.8, 0.1, 0.05, 0.05)) == "floor_right"


def test_zone_map_locate_returns_none_outside_all_zones():
    zones = {"floor_left": [(0.0, 0.0), (0.3, 0.0), (0.3, 0.3), (0.0, 0.3)]}
    zone_map = ZoneMap(zones)

    assert zone_map.locate((0.9, 0.9, 0.05, 0.05)) is None


def test_zone_map_overlap_resolves_deterministically_by_order():
    overlapping_square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    zones = {"first": overlapping_square, "second": overlapping_square}
    zone_map = ZoneMap(zones)

    assert zone_map.locate((0.1, 0.1, 0.1, 0.1)) == "first"


def test_required_zones_and_protected_zone():
    assert REQUIRED_ZONES == ("boxes", "floor_left", "floor_right")
    assert PROTECTED_ZONE == "boxes"
    assert PROTECTED_ZONE in REQUIRED_ZONES
