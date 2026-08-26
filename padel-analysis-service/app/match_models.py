from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .pose_models import PoseArtifactReference


MatchStage = Literal["unsupported", "selection_required", "tracking_complete", "completed", "failed"]
MatchConfidenceLevel = Literal["high", "medium", "low", "insufficient"]


class MatchBoundingBox(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


class MatchPlayerCandidate(BaseModel):
    candidate_id: str
    label: str
    box: MatchBoundingBox
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class MatchPreviewFrame(BaseModel):
    frame_index: int = 0
    timestamp_ms: int = 0
    width: int = 0
    height: int = 0
    candidates: list[MatchPlayerCandidate] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    artifact: PoseArtifactReference | None = None
    quality_score: float = 0.0


class MatchPreviewSummary(BaseModel):
    frame_index: int = 0
    timestamp_ms: int = 0
    width: int = 0
    height: int = 0
    candidates: list[MatchPlayerCandidate] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    frames: list[MatchPreviewFrame] = Field(default_factory=list)


class MatchTrackFrame(BaseModel):
    frame_index: int
    timestamp_ms: int
    tracked: bool
    confidence: float = 0.0
    box: MatchBoundingBox | None = None


class MatchTrackingInterval(BaseModel):
    start_frame: int
    end_frame: int
    reason: str | None = None


class MatchTrackingSummary(BaseModel):
    track_id: str
    selected_player_candidate_id: str
    selected_player_label: str | None = None
    coverage: float = 0.0
    confidence_level: MatchConfidenceLevel = "insufficient"
    total_frames: int = 0
    tracked_frames: int = 0
    missing_intervals: list[MatchTrackingInterval] = Field(default_factory=list)
    frames: list[MatchTrackFrame] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class MatchStrokeCandidateArtifacts(BaseModel):
    clip: PoseArtifactReference | None = None
    thumbnail: PoseArtifactReference | None = None


class MatchStrokeCandidate(BaseModel):
    candidate_id: str
    start_frame: int
    start_timestamp_ms: int
    peak_frame: int
    peak_timestamp_ms: int
    end_frame: int
    end_timestamp_ms: int
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    selected_player_track_confidence: float = 0.0
    rejection_reasons: list[str] = Field(default_factory=list)
    artifacts: MatchStrokeCandidateArtifacts | None = None


class MatchArtifactBundle(BaseModel):
    preview_image: PoseArtifactReference | None = None
    tracking_preview_image: PoseArtifactReference | None = None
    tracking_preview_video: PoseArtifactReference | None = None
    metadata: PoseArtifactReference | None = None


class MatchAnalysisSummary(BaseModel):
    enabled: bool = True
    available: bool = False
    stage: MatchStage = "selection_required"
    selection_required: bool = True
    selected_player_candidate_id: str | None = None
    selected_player_label: str | None = None
    preview: MatchPreviewSummary | None = None
    tracking: MatchTrackingSummary | None = None
    stroke_candidates: list[MatchStrokeCandidate] = Field(default_factory=list)
    artifacts: MatchArtifactBundle | None = None
    reasons: list[str] = Field(default_factory=list)
