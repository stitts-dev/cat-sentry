"""Zone-domain vocabulary and polygon geometry.

Named zones, point-in-polygon on a detection's bbox bottom-center, and which
zone is off-limits to the deterrent. Pure logic -- no cv2/ultralytics.

# altitude: this is the one place that knows cat-sentry's zone semantics --
# which zones must exist, and that "boxes" is protected. config.py imports
# REQUIRED_ZONES from here instead of owning that knowledge; it only
# validates config *structure* (>=3 points, coords in [0,1]), which has
# nothing to do with what a zone means.
"""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]
Polygon = Sequence[Point]
BBox = tuple[float, float, float, float]  # x, y, w, h normalized, top-left origin

REQUIRED_ZONES: tuple[str, ...] = ("boxes", "floor_left", "floor_right")

# The litterboxes themselves -- the deterrent policy (C3+) must never target
# this zone (litterbox aversion risk, see docs/design.md problem statement).
PROTECTED_ZONE = "boxes"

_EPS = 1e-9


def _on_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> bool:
    """True if (px, py) lies on the closed segment a-b, endpoints included."""
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > _EPS:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -_EPS:
        return False
    length_sq = (bx - ax) ** 2 + (by - ay) ** 2
    return dot <= length_sq + _EPS


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    """Ray-casting point-in-polygon test.

    Points exactly on an edge (or vertex) count as inside -- checked
    explicitly first so the result doesn't depend on which way the ray-cast
    floating point rounds. The ray cast itself handles concave polygons
    correctly; its only assumption is that `polygon` is simple (edges don't
    cross each other), which zone authoring guarantees.
    """
    x, y = point
    n = len(polygon)

    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        if _on_segment(x, y, ax, ay, bx, by):
            return True

    inside = False
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        if (ay > y) != (by > y):
            x_at_y = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < x_at_y:
                inside = not inside
    return inside


def bbox_bottom_center(bbox: BBox) -> Point:
    """The point a detection contributes to zone membership: bottom-center
    of its bbox, i.e. where the cat's feet are, not its centroid."""
    x, y, w, h = bbox
    return (x + w / 2, y + h)


class ZoneMap:
    """Named polygons a detection's bbox can be classified against."""

    def __init__(self, zones: dict[str, Polygon]) -> None:
        self._zones = zones

    def locate(self, bbox: BBox) -> str | None:
        """Which zone `bbox`'s bottom-center falls in, or None.

        Zones aren't expected to overlap (docs/design.md); if config ever
        violates that, the first match in dict/config-file order wins,
        deterministically.
        """
        point = bbox_bottom_center(bbox)
        for name, polygon in self._zones.items():
            if point_in_polygon(point, polygon):
                return name
        return None
