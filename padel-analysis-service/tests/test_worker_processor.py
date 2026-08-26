from __future__ import annotations

from pathlib import Path

from app.analyzer_adapter import VideoAnalysisAdapter
from app.models import JobArtifacts, JobResponse, JobResult
from app.pose_models import PoseEstimationResult
from app.worker_processor import MockWorkerProcessor
from tests.helpers import build_forehand_pose_sequence, build_synthetic_video


def test_mock_worker_processor_builds_expected_result() -> None:
    processor = MockWorkerProcessor()
    job = JobResponse(
        job_id="job-abc",
        analysis_id=77,
        status="processing",
        workflow_mode="worker",
        created_at=1.0,
        updated_at=2.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(download_url="http://example.com/video.mp4"),
        source={},
    )

    result = processor.build_result(job, "/tmp/video.avi")

    assert result.job_id == "job-abc"
    assert result.analysis_id == 77
    assert result.processor == "external_python_worker_stub"
    assert result.summary == "Mock processor completed successfully."


def test_mock_worker_processor_delegates_to_adapter() -> None:
    seen: list[tuple[str, str]] = []

    class Adapter:
        def analyze(self, job, video_path):  # type: ignore[override]
            seen.append((job.job_id, video_path))
            return JobResult(
                summary="Delegated result.",
                findings=[],
                recommendations=[],
                analysis_id=job.analysis_id,
                job_id=job.job_id,
                processor="delegated",
                service_version="1.0.0",
            )

    processor = MockWorkerProcessor(adapter=Adapter())
    job = JobResponse(
        job_id="job-def",
        analysis_id=88,
        status="processing",
        workflow_mode="worker",
        created_at=1.0,
        updated_at=2.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(download_url="http://example.com/video.mp4"),
        source={},
    )

    processor.build_result(job, "/tmp/video.avi")
    assert seen == [("job-def", "/tmp/video.avi")]


def test_video_analysis_adapter_inspects_synthetic_video(tmp_path: Path) -> None:
    video_path = tmp_path / "synthetic.avi"
    build_synthetic_video(video_path, frame_count=12, size=(64, 64), motion=True)

    job = JobResponse(
        job_id="job-real",
        analysis_id=91,
        status="processing",
        workflow_mode="worker",
        created_at=1.0,
        updated_at=2.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(download_url="http://example.com/video.mp4"),
        source={},
    )

    estimator = type(
        "FakeEstimator",
        (),
        {
            "estimate": lambda self, path: PoseEstimationResult(
                video_path=str(path),
                model_name="fake",
                model_path="/fake/model.task",
                total_frames=12,
                processed_frames=12,
                frame_rate=10.0,
                width=64,
                height=64,
                duration_seconds=1.2,
                rotation_normalized=False,
                frames=build_forehand_pose_sequence(handedness="right", frame_count=12, visibility=0.92),
            ),
        },
    )()
    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=estimator).analyze(job, str(video_path))

    assert result.summary == "Video analysis completed successfully."
    assert result.processor == "opencv_heuristic_video_analyzer"
    assert any("Resolution: 64x64" in finding for finding in result.findings)
    assert result.stroke is not None
    assert result.stroke.available is True
