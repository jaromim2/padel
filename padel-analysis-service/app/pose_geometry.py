from __future__ import annotations

from collections.abc import Iterable
from statistics import mean

from .pose_models import (
    PoseAnalysisSummary,
    PoseConfidenceLevel,
    PoseFrameRecord,
    PoseVisibilitySummary,
)

POSE_GROUPS = {
    "shoulders": ("left_shoulder", "right_shoulder"),
    "elbows": ("left_elbow", "right_elbow"),
    "wrists": ("left_wrist", "right_wrist"),
    "hips": ("left_hip", "right_hip"),
    "knees": ("left_knee", "right_knee"),
    "ankles": ("left_ankle", "right_ankle"),
}


def summarize_pose(frames: Iterable[PoseFrameRecord], total_frames: int, processed_frames: int) -> PoseAnalysisSummary:
    pose_frames = [frame for frame in frames if frame.landmarks]
    frames_with_pose = len(pose_frames)
    pose_coverage = float(frames_with_pose / processed_frames) if processed_frames > 0 else 0.0
    visibility = PoseVisibilitySummary(
        shoulders=_group_visibility(pose_frames, "shoulders"),
        elbows=_group_visibility(pose_frames, "elbows"),
        wrists=_group_visibility(pose_frames, "wrists"),
        hips=_group_visibility(pose_frames, "hips"),
        knees=_group_visibility(pose_frames, "knees"),
        ankles=_group_visibility(pose_frames, "ankles"),
    )
    confidence_level = classify_confidence(pose_coverage, visibility, frames_with_pose)
    full_body_visible = _is_full_body_visible(pose_coverage, visibility, frames_with_pose)
    reasons = build_reasons(pose_coverage, visibility, frames_with_pose, confidence_level, full_body_visible)
    return PoseAnalysisSummary(
        enabled=True,
        confidence_level=confidence_level,
        pose_coverage=round(pose_coverage, 4),
        full_body_visible=full_body_visible,
        total_frames=total_frames,
        processed_frames=processed_frames,
        frames_with_pose=frames_with_pose,
        visibility=visibility,
        reasons=reasons,
    )


def classify_confidence(
    pose_coverage: float,
    visibility: PoseVisibilitySummary,
    frames_with_pose: int,
) -> PoseConfidenceLevel:
    if frames_with_pose <= 0:
        return "insufficient"

    minimum_visibility = min(
        visibility.shoulders,
        visibility.elbows,
        visibility.wrists,
        visibility.hips,
        visibility.knees,
        visibility.ankles,
    )

    if pose_coverage >= 0.82 and minimum_visibility >= 0.75:
        return "high"
    if pose_coverage >= 0.58 and minimum_visibility >= 0.60:
        return "medium"
    if pose_coverage >= 0.20 and minimum_visibility >= 0.25:
        return "low"
    return "insufficient"


def build_reasons(
    pose_coverage: float,
    visibility: PoseVisibilitySummary,
    frames_with_pose: int,
    confidence_level: PoseConfidenceLevel,
    full_body_visible: bool,
) -> list[str]:
    reasons: list[str] = []
    if frames_with_pose <= 0:
        reasons.append("no consistent pose detection")
        return reasons

    if pose_coverage < 0.35:
        reasons.append("no consistent pose detection")
    if visibility.shoulders < 0.45 or visibility.hips < 0.45:
        reasons.append("player not fully visible")
    if visibility.ankles < 0.45:
        reasons.append("feet outside frame")
    if min(
        visibility.shoulders,
        visibility.elbows,
        visibility.wrists,
        visibility.hips,
        visibility.knees,
        visibility.ankles,
    ) < 0.55:
        reasons.append("low landmark visibility")

    if confidence_level == "insufficient" and not reasons:
        reasons.append("no consistent pose detection")
    if not full_body_visible and "player not fully visible" not in reasons:
        reasons.append("player not fully visible")
    return _dedupe(reasons)


def _group_visibility(frames: Iterable[PoseFrameRecord], group_name: str) -> float:
    landmark_names = POSE_GROUPS[group_name]
    values: list[float] = []
    for frame in frames:
        group_values = [landmark.visibility for landmark in frame.landmarks if landmark.landmark_name in landmark_names and landmark.visibility is not None]
        if group_values:
            values.append(float(mean(group_values)))
    if not values:
        return 0.0
    return float(mean(values))


def _is_full_body_visible(pose_coverage: float, visibility: PoseVisibilitySummary, frames_with_pose: int) -> bool:
    minimum_visibility = min(
        visibility.shoulders,
        visibility.elbows,
        visibility.wrists,
        visibility.hips,
        visibility.knees,
        visibility.ankles,
    )
    return frames_with_pose > 0 and pose_coverage >= 0.65 and minimum_visibility >= 0.65


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered
