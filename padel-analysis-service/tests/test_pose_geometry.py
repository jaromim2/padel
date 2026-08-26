from __future__ import annotations

from app.pose_geometry import classify_confidence, summarize_pose
from app.pose_models import PoseVisibilitySummary
from tests.helpers import build_pose_frame


def test_pose_coverage_and_confidence_classification() -> None:
    frames = [
        build_pose_frame(0, 0, 0.92),
        build_pose_frame(1, 100, 0.90),
        build_pose_frame(2, 200, 0.88),
        build_pose_frame(3, 300, 0.91),
    ]

    summary = summarize_pose(frames, total_frames=4, processed_frames=4)

    assert summary.frames_with_pose == 4
    assert summary.pose_coverage == 1.0
    assert summary.confidence_level == "high"
    assert summary.full_body_visible is True
    assert summary.visibility.shoulders > 0.9
    assert classify_confidence(0.7, PoseVisibilitySummary(shoulders=0.8, elbows=0.8, wrists=0.8, hips=0.8, knees=0.8, ankles=0.8), 4) == "medium"


def test_pose_geometry_flags_insufficient_when_visibility_is_low() -> None:
    frames = [build_pose_frame(0, 0, 0.18)]

    summary = summarize_pose(frames, total_frames=3, processed_frames=3)

    assert summary.confidence_level == "insufficient"
    assert summary.full_body_visible is False
    assert "low landmark visibility" in summary.reasons
    assert "player not fully visible" in summary.reasons
