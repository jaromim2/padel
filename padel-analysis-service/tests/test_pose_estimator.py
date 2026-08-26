from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.analyzer_adapter import VideoAnalysisAdapter
from app.pose_estimator import MediaPipePoseEstimator, PoseProcessingError
from app.pose_models import PoseEstimationResult
from tests.helpers import build_forehand_pose_sequence, build_pose_frame, build_synthetic_video


class FakePoseEstimator:
    def __init__(self, result: PoseEstimationResult) -> None:
        self._result = result

    def estimate(self, video_path: str) -> PoseEstimationResult:
        return self._result.model_copy(update={"video_path": video_path})


def test_successful_pose_extraction_with_fake_estimator(tmp_path: Path) -> None:
    video_path = tmp_path / "input.avi"
    build_synthetic_video(video_path, frame_count=12, size=(64, 64), motion=True)
    estimator = FakePoseEstimator(
        PoseEstimationResult(
            video_path=str(video_path),
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
        )
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=estimator).analyze(
        _build_job("job-1", 1),
        str(video_path),
    )

    assert result.pose is not None
    assert result.pose.confidence_level == "high"
    assert result.pose.pose_coverage == 1.0
    assert result.stroke is not None
    assert result.stroke.available is True
    assert result.stroke.dominant_hand == "right"
    assert result.stroke.phases.contact_estimate is not None
    assert result.artifacts is not None
    assert result.artifacts.landmarks is not None


def test_video_with_no_detectable_person_completes_with_insufficient_confidence(tmp_path: Path) -> None:
    video_path = tmp_path / "no-person.avi"
    build_synthetic_video(video_path, frame_count=3, size=(64, 64), motion=False)
    estimator = FakePoseEstimator(
        PoseEstimationResult(
            video_path=str(video_path),
            model_name="fake",
            model_path="/fake/model.task",
            total_frames=3,
            processed_frames=3,
            frame_rate=10.0,
            width=64,
            height=64,
            duration_seconds=0.3,
            rotation_normalized=False,
            frames=[],
        )
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=estimator).analyze(
        _build_job("job-2", 2),
        str(video_path),
    )

    assert result.pose is not None
    assert result.pose.confidence_level == "insufficient"
    assert result.pose.reasons == ["no consistent pose detection"]
    assert result.summary == "Video analysis completed successfully."


def test_partial_body_visibility_is_classified_low(tmp_path: Path) -> None:
    video_path = tmp_path / "partial.avi"
    build_synthetic_video(video_path, frame_count=12, size=(64, 64), motion=True)
    estimator = FakePoseEstimator(
        PoseEstimationResult(
            video_path=str(video_path),
            model_name="fake",
            model_path="/fake/model.task",
            total_frames=12,
            processed_frames=12,
            frame_rate=10.0,
            width=64,
            height=64,
            duration_seconds=1.2,
            rotation_normalized=False,
            frames=build_forehand_pose_sequence(
                handedness="right",
                frame_count=12,
                visibility=0.75,
                occluded_landmarks={"left_ankle", "right_ankle", "left_foot_index", "right_foot_index"},
            ),
        )
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=estimator).analyze(
        _build_job("job-3", 3),
        str(video_path),
    )

    assert result.pose is not None
    assert result.pose.confidence_level in {"low", "insufficient"}
    assert "player not fully visible" in result.pose.reasons
    assert result.stroke is not None
    assert result.stroke.available is True
    assert result.stroke.phases.contact_estimate is not None
    assert result.stroke.analysis_capabilities.stroke_phases.available is True
    assert result.stroke.analysis_capabilities.balance.available is False
    assert result.stroke.analysis_capabilities.knee_angles.left.available is False
    assert result.stroke.analysis_capabilities.knee_angles.right.available is False


def test_left_handed_mirroring_preserves_phase_detection(tmp_path: Path) -> None:
    right_video = tmp_path / "right.avi"
    left_video = tmp_path / "left.avi"
    build_synthetic_video(right_video, frame_count=12, size=(64, 64), motion=True)
    build_synthetic_video(left_video, frame_count=12, size=(64, 64), motion=True)

    right_estimator = FakePoseEstimator(
        PoseEstimationResult(
            video_path=str(right_video),
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
        )
    )
    left_estimator = FakePoseEstimator(
        PoseEstimationResult(
            video_path=str(left_video),
            model_name="fake",
            model_path="/fake/model.task",
            total_frames=12,
            processed_frames=12,
            frame_rate=10.0,
            width=64,
            height=64,
            duration_seconds=1.2,
            rotation_normalized=False,
            frames=build_forehand_pose_sequence(handedness="left", frame_count=12, visibility=0.92),
        )
    )

    right_result = VideoAnalysisAdapter(storage_root=tmp_path / "right-storage", pose_estimator=right_estimator).analyze(
        _build_job("job-right", 4, dominant_hand="right"),
        str(right_video),
    )
    left_result = VideoAnalysisAdapter(storage_root=tmp_path / "left-storage", pose_estimator=left_estimator).analyze(
        _build_job("job-left", 5, dominant_hand="left"),
        str(left_video),
    )

    assert right_result.stroke is not None
    assert left_result.stroke is not None
    assert right_result.stroke.dominant_hand == "right"
    assert left_result.stroke.dominant_hand == "left"
    assert right_result.stroke.phases.contact_estimate is not None
    assert left_result.stroke.phases.contact_estimate is not None
    assert right_result.stroke.phases.contact_estimate.frame_index == left_result.stroke.phases.contact_estimate.frame_index
    assert right_result.stroke.confidence_level == left_result.stroke.confidence_level


def test_missing_feet_do_not_block_stroke_phases(tmp_path: Path) -> None:
    video_path = tmp_path / "feet-missing.avi"
    build_synthetic_video(video_path, frame_count=16, size=(64, 64), motion=True)
    frames = build_forehand_pose_sequence(
        handedness="right",
        frame_count=16,
        visibility=0.92,
        occluded_landmarks={"left_ankle", "right_ankle", "left_heel", "right_heel", "left_foot_index", "right_foot_index"},
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator(_pose_result(video_path, frames))).analyze(
        _build_job("job-feet", 6),
        str(video_path),
    )

    assert result.stroke is not None
    assert result.stroke.available is True
    assert result.stroke.phases.available is True
    assert result.stroke.phases.contact_estimate is not None
    assert result.stroke.analysis_capabilities.stroke_phases.available is True
    assert result.stroke.analysis_capabilities.balance.available is False


def test_missing_left_ankle_only_blocks_left_knee_and_balance(tmp_path: Path) -> None:
    video_path = tmp_path / "left-ankle-missing.avi"
    build_synthetic_video(video_path, frame_count=16, size=(64, 64), motion=True)
    frames = build_forehand_pose_sequence(
        handedness="right",
        frame_count=16,
        visibility=0.92,
        occluded_landmarks={"left_ankle", "left_heel", "left_foot_index"},
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator(_pose_result(video_path, frames))).analyze(
        _build_job("job-left-ankle", 7),
        str(video_path),
    )

    assert result.stroke is not None
    assert result.stroke.available is True
    assert result.stroke.phases.contact_estimate is not None
    assert result.stroke.analysis_capabilities.knee_angles.left.available is False
    assert result.stroke.analysis_capabilities.knee_angles.right.available is True
    assert result.stroke.analysis_capabilities.balance.available is False


def test_short_gap_is_interpolated_for_stroke_detection(tmp_path: Path) -> None:
    video_path = tmp_path / "short-gap.avi"
    build_synthetic_video(video_path, frame_count=16, size=(64, 64), motion=True)
    frames = build_forehand_pose_sequence(handedness="right", frame_count=16, visibility=0.92)
    _set_visibility_for_frames(
        frames,
        frame_indices={6, 7},
        landmark_names={"right_shoulder", "right_elbow", "right_wrist"},
        visibility=0.05,
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator(_pose_result(video_path, frames))).analyze(
        _build_job("job-short-gap", 8),
        str(video_path),
    )

    assert result.stroke is not None
    assert result.stroke.available is True
    assert result.stroke.phases.contact_estimate is not None
    assert result.stroke.analysis_capabilities.stroke_phases.available is True


def test_long_gap_around_contact_blocks_contact_estimate(tmp_path: Path) -> None:
    video_path = tmp_path / "long-gap.avi"
    build_synthetic_video(video_path, frame_count=16, size=(64, 64), motion=True)
    frames = build_forehand_pose_sequence(handedness="right", frame_count=16, visibility=0.92)
    _set_visibility_for_frames(
        frames,
        frame_indices={5, 6, 7, 8, 9, 10},
        landmark_names={"right_shoulder", "right_elbow", "right_wrist"},
        visibility=0.02,
    )

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator(_pose_result(video_path, frames))).analyze(
        _build_job("job-long-gap", 9),
        str(video_path),
    )

    assert result.stroke is not None
    assert result.stroke.available is False
    assert result.stroke.phases.contact_estimate is None
    assert result.stroke.reasons
    assert result.stroke.analysis_capabilities.stroke_phases.available is False


def test_real_mediapipe_pose_estimator_is_opt_in(tmp_path: Path) -> None:
    model_path_env = os.getenv("POSE_MODEL_PATH")
    sample_video_env = os.getenv("POSE_TEST_VIDEO_PATH")
    if not model_path_env or not sample_video_env:
        pytest.skip("Real MediaPipe pose test requires POSE_MODEL_PATH and POSE_TEST_VIDEO_PATH.")
    model_path = Path(model_path_env)
    sample_video = Path(sample_video_env)
    if not model_path.exists() or not sample_video.exists():
        pytest.skip("Real MediaPipe pose test requires POSE_MODEL_PATH and POSE_TEST_VIDEO_PATH.")

    result = MediaPipePoseEstimator(model_path=str(model_path)).estimate(str(sample_video))

    assert result.processed_frames > 0
    assert result.frames is not None


def test_pose_processing_error_serializes_code() -> None:
    error = PoseProcessingError(code="pose_video_open_failed", message="Unable to open video file.")
    assert error.code == "pose_video_open_failed"
    assert str(error) == "Unable to open video file."


def _build_job(job_id: str, analysis_id: int, *, dominant_hand: str = "right", shot_type: str = "forehand"):
    from app.models import JobArtifacts, JobResponse

    return JobResponse(
        job_id=job_id,
        analysis_id=analysis_id,
        status="processing",
        workflow_mode="worker",
        created_at=1.0,
        updated_at=2.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(download_url="http://example.com/video.mp4"),
        source={"dominant_hand": dominant_hand, "shot_type": shot_type},
    )


def _pose_result(video_path: Path, frames):
    return PoseEstimationResult(
        video_path=str(video_path),
        model_name="fake",
        model_path="/fake/model.task",
        total_frames=len(frames),
        processed_frames=len(frames),
        frame_rate=10.0,
        width=64,
        height=64,
        duration_seconds=max(0.1, len(frames) / 10.0),
        rotation_normalized=False,
        frames=frames,
    )


def _set_visibility_for_frames(frames, *, frame_indices: set[int], landmark_names: set[str], visibility: float) -> None:
    for frame in frames:
        if frame.frame_index not in frame_indices:
            continue
        for landmark in frame.landmarks:
            if landmark.landmark_name in landmark_names:
                landmark.visibility = visibility
                landmark.presence = visibility
