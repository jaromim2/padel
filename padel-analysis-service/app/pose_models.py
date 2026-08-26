from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PoseConfidenceLevel = Literal["high", "medium", "low", "insufficient"]

POSE_LANDMARK_NAMES = [
    "nose",
    "left_eye_inner",
    "left_eye",
    "left_eye_outer",
    "right_eye_inner",
    "right_eye",
    "right_eye_outer",
    "left_ear",
    "right_ear",
    "mouth_left",
    "mouth_right",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_pinky",
    "right_pinky",
    "left_index",
    "right_index",
    "left_thumb",
    "right_thumb",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
    "left_heel",
    "right_heel",
    "left_foot_index",
    "right_foot_index",
]


class PoseLandmarkRecord(BaseModel):
    frame_index: int
    timestamp_ms: int
    landmark_name: str
    x: float
    y: float
    z: float
    visibility: float | None = None
    presence: float | None = None


class PoseFrameRecord(BaseModel):
    frame_index: int
    timestamp_ms: int
    landmarks: list[PoseLandmarkRecord] = Field(default_factory=list)


class PoseVisibilitySummary(BaseModel):
    shoulders: float = 0.0
    elbows: float = 0.0
    wrists: float = 0.0
    hips: float = 0.0
    knees: float = 0.0
    ankles: float = 0.0


class PoseArtifactReference(BaseModel):
    type: str
    storage_key: str
    mime_type: str
    filename: str | None = None
    frame_index: int | None = None
    timestamp_ms: int | None = None
    size_bytes: int | None = None


class PoseArtifactBundle(BaseModel):
    landmarks: PoseArtifactReference | None = None
    metadata: PoseArtifactReference | None = None
    annotated_video: PoseArtifactReference | None = None
    annotated_keyframes: list[PoseArtifactReference] = Field(default_factory=list)


class PoseAnalysisSummary(BaseModel):
    enabled: bool = True
    confidence_level: PoseConfidenceLevel = "insufficient"
    pose_coverage: float = 0.0
    full_body_visible: bool = False
    total_frames: int = 0
    processed_frames: int = 0
    frames_with_pose: int = 0
    visibility: PoseVisibilitySummary = Field(default_factory=PoseVisibilitySummary)
    reasons: list[str] = Field(default_factory=list)


class PoseVideoSummary(BaseModel):
    total_frames: int = 0
    processed_frames: int = 0
    frame_rate: float = 0.0
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0
    motion_score: float = 0.0
    motion_label: str = "stable"
    rotation_normalized: bool = False


class PoseEstimationResult(BaseModel):
    video_path: str
    model_name: str
    model_path: str
    total_frames: int
    processed_frames: int
    frame_rate: float
    width: int
    height: int
    duration_seconds: float
    rotation_normalized: bool = False
    frames: list[PoseFrameRecord] = Field(default_factory=list)


class PoseAnalysisArtifacts(BaseModel):
    pose: PoseArtifactBundle = Field(default_factory=PoseArtifactBundle)
    video: PoseArtifactBundle | None = None
    note: dict[str, Any] | None = None
