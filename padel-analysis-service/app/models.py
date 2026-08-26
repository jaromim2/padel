from __future__ import annotations

from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field

from .match_models import MatchAnalysisSummary
from .pose_models import PoseAnalysisSummary, PoseArtifactBundle, PoseVideoSummary
from .technical_feedback_models import TechnicalFeedbackSummary
from .stroke_models import StrokeAnalysisSummary


class JobCreateRequest(BaseModel):
    analysis_id: int = Field(gt=0)
    owner_user_id: int = Field(gt=0)
    analysis_mode: str = "stroke"
    shot_type: str
    dominant_hand: str
    camera_angle: str
    video_download_url: AnyHttpUrl
    video_name: str
    video_mime_type: str
    video_size: int = Field(ge=0)
    video_duration_seconds: float = Field(gt=0)
    video_sha256: str
    submission_fingerprint: str


class JobError(BaseModel):
    code: str
    message: str
    detail: str | dict[str, Any] | list[Any] | None = None


class JobArtifacts(BaseModel):
    download_url: AnyHttpUrl | str | None = None
    downloaded_video_path: str = ""
    downloaded_video_size: int = 0
    downloaded_video_sha256: str = ""


class JobResult(BaseModel):
    job_status: str = "completed"
    summary: str
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    analysis_id: int
    job_id: str
    processor: str
    service_version: str
    video: PoseVideoSummary | None = None
    pose: PoseAnalysisSummary | None = None
    stroke: StrokeAnalysisSummary | None = None
    technical_feedback: TechnicalFeedbackSummary | None = None
    match: MatchAnalysisSummary | None = None
    artifacts: PoseArtifactBundle | None = None


class JobResponse(BaseModel):
    job_id: str
    analysis_id: int
    status: str
    workflow_mode: str
    created_at: float
    updated_at: float
    result: JobResult | None = None
    error: JobError | None = None
    external_artifacts: JobArtifacts = Field(default_factory=JobArtifacts)
    source: dict[str, Any] = Field(default_factory=dict)


class JobStatusUpdateRequest(BaseModel):
    status: str
    result: JobResult | None = None
    error: JobError | None = None
    external_artifacts: JobArtifacts | None = None


class JobSelectedPlayerRequest(BaseModel):
    selected_player_candidate_id: str = Field(min_length=1)
    video_download_url: AnyHttpUrl | None = None


class JobContactFrameUpdateRequest(BaseModel):
    frame_index: int = Field(ge=0)
