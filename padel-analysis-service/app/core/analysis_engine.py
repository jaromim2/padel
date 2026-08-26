from __future__ import annotations

from typing import Protocol

from ..analyzer_adapter import VideoAnalysisAdapter
from ..match_adapter import MatchAnalysisAdapter
from ..models import JobArtifacts, JobResponse, JobResult
from ..stroke_analysis import ForehandStrokeAnalyzer, StrokeAnalysisConfig
from .config import Settings


class AnalysisEngine(Protocol):
    def process(self, job: dict[str, object]) -> dict[str, object]:
        ...


class MockAnalysisEngine:
    def __init__(self, service_version: str) -> None:
        self._service_version = service_version

    def process(self, job: dict[str, object]) -> dict[str, object]:
        result = JobResult(
            job_status="completed",
            summary="Mock processor completed successfully.",
            findings=[
                "External boundary verified.",
                f"Video was accepted for analysis job {job['job_id']}.",
            ],
            recommendations=[
                "Continue with the same camera angle for future captures.",
                "This result is a placeholder until the real analyzer is connected.",
            ],
            analysis_id=int(job["analysis_id"]),
            job_id=str(job["job_id"]),
            processor="mock_python_service",
            service_version=self._service_version,
        )
        return result.model_dump()


class RealAnalysisEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._adapter = VideoAnalysisAdapter(
            storage_root=settings.storage_dir,
            pose_model_path=settings.pose_model_path,
            stroke_analyzer=ForehandStrokeAnalyzer(
                StrokeAnalysisConfig(smoothing_alpha=settings.stroke_smoothing_alpha)
            ),
        )
        self._match_adapter = MatchAnalysisAdapter(storage_root=settings.storage_dir)

    def process(self, job: dict[str, object]) -> dict[str, object]:
        download_path = str(job.get("downloaded_video_path", "")).strip()
        if not download_path:
            raise ValueError("downloaded_video_path is required for real analysis")

        analysis_mode = str(job.get("analysis_mode", "stroke")).strip().lower() or "stroke"

        response = JobResponse(
            job_id=str(job["job_id"]),
            analysis_id=int(job["analysis_id"]),
            status=str(job["status"]),
            workflow_mode=str(job["workflow_mode"]),
            created_at=float(job["created_at"]),
            updated_at=float(job["updated_at"]),
            result=None,
            error=None,
            external_artifacts=JobArtifacts(
                download_url=str(job["download_url"]),
                downloaded_video_path=download_path,
                downloaded_video_size=int(job.get("downloaded_video_size", 0)),
                downloaded_video_sha256=str(job.get("downloaded_video_sha256", "")),
            ),
            source=dict(job.get("payload", {})),
        )

        if analysis_mode == "match":
            result = self._match_adapter.analyze(response, download_path)
        else:
            result = self._adapter.analyze(response, download_path)
        if result.service_version == "0.1.0":
            result = result.model_copy(update={"service_version": self._settings.service_version})
        return result.model_dump()
