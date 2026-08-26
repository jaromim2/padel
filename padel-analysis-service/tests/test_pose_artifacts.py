from __future__ import annotations

import json
from pathlib import Path

from app.pose_artifacts import PoseArtifactWriter
from app.pose_geometry import summarize_pose
from app.pose_models import PoseEstimationResult
from tests.helpers import build_pose_frame, build_synthetic_video


def _build_pose_result(video_path: Path) -> PoseEstimationResult:
    frames = [
        build_pose_frame(0, 0, 0.92),
        build_pose_frame(1, 100, 0.88),
        build_pose_frame(2, 200, 0.90),
    ]
    return PoseEstimationResult(
        video_path=str(video_path),
        model_name="pose_landmarker_lite",
        model_path="/models/pose.task",
        total_frames=3,
        processed_frames=3,
        frame_rate=10.0,
        width=64,
        height=64,
        duration_seconds=0.3,
        rotation_normalized=False,
        frames=frames,
    )


def test_pose_artifacts_write_landmarks_json_and_annotated_output(tmp_path: Path) -> None:
    video_path = tmp_path / "input.avi"
    build_synthetic_video(video_path, frame_count=3, size=(64, 64), motion=True)

    pose_result = _build_pose_result(video_path)
    summary = summarize_pose(pose_result.frames, pose_result.total_frames, pose_result.processed_frames)
    writer = PoseArtifactWriter(tmp_path / "storage")
    bundle = writer.write_bundle(
        job_id="job-123",
        pose_result=pose_result,
        pose_summary=summary,
        video_path=str(video_path),
    )

    assert bundle.landmarks is not None
    assert bundle.landmarks.storage_key.endswith("landmarks.json")
    landmarks_path = tmp_path / "storage" / bundle.landmarks.storage_key
    assert landmarks_path.exists()
    payload = json.loads(landmarks_path.read_text(encoding="utf-8"))
    assert payload["summary"]["confidence_level"] == "high"
    assert payload["frames"][0]["landmarks"][0]["landmark_name"] == "left_shoulder"
    assert bundle.annotated_video is not None or bundle.annotated_keyframes
