from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class Point2D:
    x: float
    y: float


def distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def midpoint(a: Point2D, b: Point2D) -> Point2D:
    return Point2D(x=(a.x + b.x) / 2.0, y=(a.y + b.y) / 2.0)


def three_point_joint_angle(a: Point2D, b: Point2D, c: Point2D) -> float | None:
    ab_x = a.x - b.x
    ab_y = a.y - b.y
    cb_x = c.x - b.x
    cb_y = c.y - b.y

    ab_length = math.hypot(ab_x, ab_y)
    cb_length = math.hypot(cb_x, cb_y)
    if ab_length == 0.0 or cb_length == 0.0:
        return None

    cosine = ((ab_x * cb_x) + (ab_y * cb_y)) / (ab_length * cb_length)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def line_orientation(a: Point2D, b: Point2D) -> float | None:
    delta_x = b.x - a.x
    delta_y = a.y - b.y
    if delta_x == 0.0 and delta_y == 0.0:
        return None
    return math.degrees(math.atan2(delta_y, delta_x))


def normalize_angle_degrees(value: float) -> float:
    normalized = ((value + 180.0) % 360.0) - 180.0
    if normalized == -180.0 and value > 0.0:
        return 180.0
    return normalized


def shortest_angle_delta(previous: float, current: float) -> float:
    return normalize_angle_degrees(current - previous)


def velocity(previous: Point2D, current: Point2D, previous_timestamp_ms: int, current_timestamp_ms: int) -> float | None:
    delta_ms = current_timestamp_ms - previous_timestamp_ms
    if delta_ms <= 0:
        return None
    return distance(previous, current) / (delta_ms / 1000.0)


def ema(previous: Point2D | None, current: Point2D, alpha: float) -> Point2D:
    if previous is None:
        return current
    alpha = max(0.0, min(1.0, alpha))
    return Point2D(
        x=(alpha * current.x) + ((1.0 - alpha) * previous.x),
        y=(alpha * current.y) + ((1.0 - alpha) * previous.y),
    )


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))
