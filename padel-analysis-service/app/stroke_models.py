from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


StrokeConfidenceLevel = Literal["high", "medium", "low", "insufficient"]
StrokeHandedness = Literal["left", "right"]
StrokeConfidenceSource = Literal["native", "legacy_derived"]


class StrokeSmoothingSummary(BaseModel):
    enabled: bool = True
    method: str = "ema"
    alpha: float = 0.35
    notes: list[str] = Field(default_factory=list)


class StrokeLandmarkQualitySummary(BaseModel):
    landmark_name: str
    frame_count: int = 0
    median_visibility: float | None = None
    lower_percentile_visibility: float | None = None
    usable_frame_percentage: float | None = None
    longest_missing_gap_frames: int = 0
    interpolated_frames: int = 0
    motion_continuity: float | None = None
    required_visibility: float | None = None
    required_usable_frame_percentage: float | None = None
    allowed_gap_frames: int | None = None


class StrokeCapabilityAssessment(BaseModel):
    available: bool = False
    confidence_level: StrokeConfidenceLevel = "insufficient"
    confidence_source: StrokeConfidenceSource = "native"
    required_landmarks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    diagnostics: list[StrokeLandmarkQualitySummary] = Field(default_factory=list)


class StrokeKneeCapabilitySummary(BaseModel):
    left: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)
    right: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)


class StrokeAnalysisCapabilitySummary(BaseModel):
    stroke_phases: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)
    elbow_angles: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)
    shoulder_rotation: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)
    hip_rotation: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)
    knee_angles: StrokeKneeCapabilitySummary = Field(default_factory=StrokeKneeCapabilitySummary)
    balance: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)
    recovery: StrokeCapabilityAssessment = Field(default_factory=StrokeCapabilityAssessment)


class StrokePhaseEvent(BaseModel):
    frame_index: int | None = None
    timestamp_ms: int | None = None
    confidence: float | None = None
    evidence: list[str] = Field(default_factory=list)


class StrokeContactFrame(StrokePhaseEvent):
    source: Literal["estimated", "manual", "unavailable"] = "estimated"
    candidate_start_frame: int | None = None
    candidate_end_frame: int | None = None


class StrokeAngleMeasurement(BaseModel):
    available: bool = False
    frame_index: int | None = None
    timestamp_ms: int | None = None
    value_degrees: float | None = None
    raw_value_degrees: float | None = None
    evidence: list[str] = Field(default_factory=list)


class StrokeNormalizedMeasurement(BaseModel):
    available: bool = False
    frame_index: int | None = None
    timestamp_ms: int | None = None
    value: float | None = None
    evidence: list[str] = Field(default_factory=list)


class StrokePhaseMetricTriple(BaseModel):
    preparation: StrokeAngleMeasurement | None = None
    contact: StrokeAngleMeasurement | None = None
    follow_through: StrokeAngleMeasurement | None = None


class StrokeKneeAnglesAtContact(BaseModel):
    left: StrokeAngleMeasurement | None = None
    right: StrokeAngleMeasurement | None = None


class StrokeWristVelocityProfile(BaseModel):
    peak_normalized_velocity: float | None = None
    peak_frame_index: int | None = None
    peak_timestamp_ms: int | None = None
    velocity_at_contact: float | None = None
    peak_acceleration: float | None = None
    peak_acceleration_frame_index: int | None = None
    reliable: bool = False
    evidence: list[str] = Field(default_factory=list)


class StrokeBalanceProxySummary(BaseModel):
    proxy_score: float | None = None
    body_center_drift: float | None = None
    foot_support_range: float | None = None
    reliable: bool = False
    reasons: list[str] = Field(default_factory=list)


class StrokeMetricsSummary(BaseModel):
    dominant_elbow_angle: StrokePhaseMetricTriple = Field(default_factory=StrokePhaseMetricTriple)
    shoulder_line_orientation: StrokePhaseMetricTriple = Field(default_factory=StrokePhaseMetricTriple)
    hip_line_orientation: StrokePhaseMetricTriple = Field(default_factory=StrokePhaseMetricTriple)
    knee_angles_at_contact: StrokeKneeAnglesAtContact = Field(default_factory=StrokeKneeAnglesAtContact)
    wrist_velocity_profile: StrokeWristVelocityProfile = Field(default_factory=StrokeWristVelocityProfile)
    recovery_duration_ms: int | None = None
    balance_proxy: StrokeBalanceProxySummary = Field(default_factory=StrokeBalanceProxySummary)


class StrokePhasesSummary(BaseModel):
    available: bool = False
    confidence_level: StrokeConfidenceLevel = "insufficient"
    ready: StrokePhaseEvent | None = None
    preparation_start: StrokePhaseEvent | None = None
    backswing_end: StrokePhaseEvent | None = None
    contact_estimate: StrokeContactFrame | None = None
    original_contact_estimate: StrokeContactFrame | None = None
    follow_through_peak: StrokePhaseEvent | None = None
    recovery: StrokePhaseEvent | None = None
    reasons: list[str] = Field(default_factory=list)


class StrokeAnalysisSummary(BaseModel):
    enabled: bool = True
    available: bool = False
    dominant_hand: StrokeHandedness = "right"
    confidence_level: StrokeConfidenceLevel = "insufficient"
    smoothing: StrokeSmoothingSummary = Field(default_factory=StrokeSmoothingSummary)
    analysis_capabilities: StrokeAnalysisCapabilitySummary = Field(default_factory=StrokeAnalysisCapabilitySummary)
    phases: StrokePhasesSummary = Field(default_factory=StrokePhasesSummary)
    metrics: StrokeMetricsSummary | None = None
    reasons: list[str] = Field(default_factory=list)
