from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TechnicalFeedbackConfidenceLevel = Literal["high", "medium", "low", "insufficient"]
TechnicalFeedbackClassification = Literal["confirmed_observation", "possible_observation", "neutral_measurement"]
TechnicalFeedbackImportance = Literal["primary", "secondary", "neutral"]
TechnicalFeedbackRuleStatus = Literal["experimental", "stable"]
TechnicalFeedbackOutputKind = Literal["finding", "possible_observation", "neutral_measurement"]
TechnicalFeedbackComparison = Literal[
    "less_than",
    "less_than_or_equal",
    "greater_than",
    "greater_than_or_equal",
    "absolute_less_than",
    "absolute_greater_than",
    "ratio_less_than",
    "ratio_greater_than",
]


class TechnicalFeedbackRuleConfig(BaseModel):
    id: str
    family: str
    metric: str
    comparison: TechnicalFeedbackComparison
    provisional_threshold: float
    minimum_capability_confidence: TechnicalFeedbackConfidenceLevel
    explanation: str
    status: TechnicalFeedbackRuleStatus = "experimental"
    version: str
    unit: str
    notes: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    supported_camera_angles: list[str] = Field(default_factory=list)
    output_kind: TechnicalFeedbackOutputKind = "finding"


class TechnicalFeedbackRuleSet(BaseModel):
    version: str
    rules: list[TechnicalFeedbackRuleConfig] = Field(default_factory=list)


class TechnicalFeedbackEvidence(BaseModel):
    label: str
    frame_index: int | None = None
    timestamp_ms: int | None = None
    value: float | str | bool | None = None
    threshold: float | None = None
    note: str | None = None


class TechnicalFeedbackPhaseReference(BaseModel):
    phase: str
    frame_index: int | None = None
    timestamp_ms: int | None = None


class TechnicalFeedbackFinding(BaseModel):
    rule_id: str
    family: str
    title: str
    kind: TechnicalFeedbackOutputKind = "finding"
    classification: TechnicalFeedbackClassification
    confidence_level: TechnicalFeedbackConfidenceLevel
    importance: TechnicalFeedbackImportance = "primary"
    observation: str
    metric_name: str
    measured_value: float | None = None
    threshold_value: float | None = None
    unit: str | None = None
    comparison: TechnicalFeedbackComparison
    primary_frame_index: int | None = None
    primary_timestamp_ms: int | None = None
    evidence: list[TechnicalFeedbackEvidence] = Field(default_factory=list)
    phase_references: list[TechnicalFeedbackPhaseReference] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    supporting_details: dict[str, float | str | bool] = Field(default_factory=dict)
    confidence_source: str = "native"
    confidence_ceiling: TechnicalFeedbackConfidenceLevel | None = None
    dependency_confidence: TechnicalFeedbackConfidenceLevel | None = None
    camera_angle: str | None = None


class TechnicalFeedbackWithheldFinding(BaseModel):
    rule_id: str
    family: str
    title: str
    reason: str
    missing_capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    supporting_details: dict[str, float | str | bool] = Field(default_factory=dict)


class TechnicalFeedbackSummary(BaseModel):
    available: bool = False
    rules_version: str = ""
    source_analysis_id: int | None = None
    calculated_at: str | None = None
    generation_mode: Literal["normal", "backfill"] = "normal"
    legacy_derived: bool = False
    findings: list[TechnicalFeedbackFinding] = Field(default_factory=list)
    possible_observations: list[TechnicalFeedbackFinding] = Field(default_factory=list)
    neutral_measurements: list[TechnicalFeedbackFinding] = Field(default_factory=list)
    withheld_findings: list[TechnicalFeedbackWithheldFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
