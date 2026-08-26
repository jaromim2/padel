from __future__ import annotations

from app.stroke_geometry import (
    Point2D,
    clamp_unit,
    distance,
    ema,
    line_orientation,
    midpoint,
    normalize_angle_degrees,
    shortest_angle_delta,
    three_point_joint_angle,
    velocity,
)


def test_geometry_helpers_compute_expected_values() -> None:
    a = Point2D(0.0, 0.0)
    b = Point2D(3.0, 4.0)
    c = Point2D(3.0, 0.0)

    assert distance(a, b) == 5.0
    assert midpoint(a, b) == Point2D(1.5, 2.0)
    assert round(three_point_joint_angle(Point2D(1.0, 0.0), Point2D(0.0, 0.0), Point2D(0.0, 1.0)), 2) == 90.0
    assert round(line_orientation(Point2D(0.0, 0.0), Point2D(1.0, 0.0)), 2) == 0.0
    assert velocity(a, b, 0, 1000) == 5.0
    assert ema(Point2D(0.0, 0.0), Point2D(1.0, 1.0), 0.25) == Point2D(0.25, 0.25)
    assert clamp_unit(-0.2) == 0.0
    assert clamp_unit(1.4) == 1.0


def test_ema_respects_previous_point() -> None:
    previous = Point2D(0.2, 0.6)
    current = Point2D(0.8, 0.1)
    smoothed = ema(previous, current, 0.5)
    assert smoothed == Point2D(0.5, 0.35)


def test_angle_normalization_across_boundary() -> None:
    assert shortest_angle_delta(179.0, -175.0) == 6.0
    assert shortest_angle_delta(-178.0, 176.0) == -6.0
    assert shortest_angle_delta(15.0, 20.0) == 5.0
    assert normalize_angle_degrees(181.0) == -179.0
