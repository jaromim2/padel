from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .models import JobArtifacts, JobResponse, JobResult
from .pose_artifacts import PoseArtifactWriter
from .pose_estimator import MediaPipePoseEstimator, PoseEstimator, PoseProcessingError
from .pose_geometry import build_reasons, classify_confidence, summarize_pose
from .pose_models import PoseAnalysisSummary, PoseEstimationResult, PoseVideoSummary
from .stroke_analysis import ForehandStrokeAnalyzer, StrokeAnalysisConfig, StrokeProcessingError
from .stroke_models import StrokeAnalysisSummary
from .technical_feedback import TechnicalFeedbackEngine


class AnalyzerAdapter(Protocol):
    def analyze(self, job: JobResponse, video_path: str) -> JobResult:
        ...


@dataclass(frozen=True, slots=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float


class MockAnalyzerAdapter:
    def analyze(self, job: JobResponse, video_path: str) -> JobResult:
        return JobResult(
            summary="Mock processor completed successfully.",
            findings=[
                "Mock worker consumed the job contract.",
                f"Input video path: {video_path}",
                f"Job {job.job_id} was processed by the worker stub.",
            ],
            recommendations=[
                "Replace the stub with the Python video analyzer when ready.",
                "Keep the existing WordPress polling flow unchanged.",
            ],
            analysis_id=job.analysis_id,
            job_id=job.job_id,
            processor="external_python_worker_stub",
            service_version=job.result.service_version if job.result else "0.1.0",
            video=None,
            pose=None,
            stroke=None,
            artifacts=None,
        )


class VideoAnalysisAdapter:
    def __init__(
        self,
        *,
        storage_root: Path | None = None,
        pose_estimator: PoseEstimator | None = None,
        pose_model_path: str | None = None,
        stroke_analyzer: ForehandStrokeAnalyzer | None = None,
        technical_feedback_engine: TechnicalFeedbackEngine | None = None,
    ) -> None:
        self._storage_root = storage_root or Path(os.getenv("PADEL_ANALYSIS_SERVICE_STORAGE_DIR", "/tmp/padel-analysis-service/storage"))
        self._pose_estimator = pose_estimator or self._build_default_pose_estimator(pose_model_path)
        self._stroke_analyzer = stroke_analyzer or self._build_default_stroke_analyzer()
        self._technical_feedback_engine = technical_feedback_engine or TechnicalFeedbackEngine()

    def analyze(self, job: JobResponse, video_path: str) -> JobResult:
        probe = self._probe(video_path)
        motion_score = self._motion_score(video_path, probe)
        pose_result = self._pose_estimator.estimate(video_path)
        pose_summary = summarize_pose(pose_result.frames, pose_result.total_frames, pose_result.processed_frames)
        stroke_summary = self._stroke_analyzer.analyze(job, pose_result, pose_summary)
        technical_feedback = self._technical_feedback_engine.build(
            job,
            stroke_summary,
            pose_summary,
            source_analysis_id=job.analysis_id,
            generation_mode="normal",
            calculated_at=datetime.now(UTC).isoformat(),
        )
        artifacts = self._write_artifacts(job, pose_result, pose_summary, video_path)

        findings = [
            f"Duration: {probe.duration_seconds:.2f} seconds",
            f"Resolution: {probe.width}x{probe.height}",
            f"Frame rate: {probe.fps:.2f} fps",
            f"Estimated motion score: {motion_score:.1f}/100",
            f"Camera stability: {self._stability_label(motion_score)}",
            f"Pose coverage: {pose_summary.pose_coverage:.2%}",
            f"Pose confidence: {pose_summary.confidence_level}",
        ]
        if pose_summary.reasons:
            findings.extend(f"Pose note: {reason}" for reason in pose_summary.reasons)

        recommendations = self._recommendations(motion_score, pose_summary)

        video_summary = PoseVideoSummary(
            total_frames=probe.frame_count,
            processed_frames=pose_result.processed_frames,
            frame_rate=probe.fps,
            width=probe.width,
            height=probe.height,
            duration_seconds=probe.duration_seconds,
            motion_score=round(motion_score, 4),
            motion_label=self._stability_label(motion_score),
            rotation_normalized=pose_result.rotation_normalized,
        )

        return JobResult(
            summary="Video analysis completed successfully.",
            findings=findings,
            recommendations=recommendations,
            analysis_id=job.analysis_id,
            job_id=job.job_id,
            processor="opencv_heuristic_video_analyzer",
            service_version=job.result.service_version if job.result else "0.1.0",
            video=video_summary,
            pose=pose_summary,
            stroke=stroke_summary,
            technical_feedback=technical_feedback,
            artifacts=artifacts,
        )

    def _build_default_pose_estimator(self, pose_model_path: str | None) -> PoseEstimator:
        model_path = (pose_model_path or os.getenv("POSE_MODEL_PATH", "")).strip()
        return MediaPipePoseEstimator(
            model_path=model_path,
            min_pose_detection_confidence=float(os.getenv("POSE_MIN_DETECTION_CONFIDENCE", "0.5")),
            min_pose_presence_confidence=float(os.getenv("POSE_MIN_PRESENCE_CONFIDENCE", "0.5")),
            min_tracking_confidence=float(os.getenv("POSE_MIN_TRACKING_CONFIDENCE", "0.5")),
            max_num_poses=1,
        )

    def _build_default_stroke_analyzer(self) -> ForehandStrokeAnalyzer:
        return ForehandStrokeAnalyzer(
            StrokeAnalysisConfig(
                smoothing_alpha=float(os.getenv("STROKE_SMOOTHING_ALPHA", "0.35")),
            )
        )

    def _write_artifacts(
        self,
        job: JobResponse,
        pose_result: PoseEstimationResult,
        pose_summary: PoseAnalysisSummary,
        video_path: str,
    ):
        try:
            writer = PoseArtifactWriter(self._storage_root)
            return writer.write_bundle(
                job_id=job.job_id,
                pose_result=pose_result,
                pose_summary=pose_summary,
                video_path=video_path,
            )
        except PoseProcessingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PoseProcessingError(
                code="pose_artifact_write_failed",
                message="Unable to write pose artifacts.",
                detail={"type": exc.__class__.__name__},
            ) from exc

    def _probe(self, video_path: str) -> VideoProbe:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise PoseProcessingError(code="pose_video_open_failed", message="Unable to open video file.")

        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)

        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()

        duration_seconds = float(frame_count / fps) if frame_count > 0 and fps > 0 else 0.0
        if width <= 0 or height <= 0:
            raise PoseProcessingError(code="pose_video_probe_failed", message="Unable to determine video dimensions.")
        if frame_count <= 0:
            raise PoseProcessingError(code="pose_video_probe_failed", message="Unable to determine video duration.")

        return VideoProbe(width=width, height=height, fps=fps, frame_count=frame_count, duration_seconds=duration_seconds)

    def _motion_score(self, video_path: str, probe: VideoProbe) -> float:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise PoseProcessingError(code="pose_video_open_failed", message="Unable to reopen video file for analysis.")

        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)

        sample_count = min(12, probe.frame_count)
        if sample_count < 2:
            sample_count = min(2, probe.frame_count)
        frame_indices = np.linspace(0, max(probe.frame_count - 1, 0), num=sample_count, dtype=int)

        previous_frame: np.ndarray | None = None
        motion_values: list[float] = []
        for index in frame_indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
            current_frame = gray.astype(np.float32)
            if previous_frame is not None:
                motion_values.append(float(np.mean(np.abs(current_frame - previous_frame))))
            previous_frame = current_frame

        capture.release()
        if not motion_values:
            return 0.0

        average_motion = float(sum(motion_values) / len(motion_values))
        return min(100.0, (average_motion / 255.0) * 100.0)

    def _stability_label(self, motion_score: float) -> str:
        if motion_score < 6.0:
            return "stable"
        if motion_score < 18.0:
            return "moderate"
        return "active"

    def _recommendations(self, motion_score: float, pose_summary: PoseAnalysisSummary) -> list[str]:
        recommendations: list[str] = []
        if motion_score < 6.0:
            recommendations.extend(
                [
                    "Camera movement is low, so this clip is suitable for comparison runs.",
                    "You can now add pose analysis on top of this stable capture.",
                ]
            )
        elif motion_score < 18.0:
            recommendations.extend(
                [
                    "Camera motion is moderate; a slightly steadier setup would improve consistency.",
                    "Keep the same angle if you want reliable comparisons between sessions.",
                ]
            )
        else:
            recommendations.extend(
                [
                    "The camera moved a lot during the clip; use a tripod or stronger mount next time.",
                    "A steadier capture will improve later analysis.",
                ]
            )

        if pose_summary.confidence_level == "insufficient" and pose_summary.reasons:
            recommendations.extend(f"Pose limitation: {reason}" for reason in pose_summary.reasons)
        return recommendations
