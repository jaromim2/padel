from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.match_adapter import MatchAnalysisAdapter
from app.match_components import TrackingObservation
from app.match_models import MatchBoundingBox, MatchPlayerCandidate
from app.models import JobArtifacts, JobResponse
from tests.helpers import build_synthetic_video


class FakeDetector:
    def detect(self, frame):  # type: ignore[override]
        height, width = frame.shape[:2]
        box_width = max(20, width // 6)
        box_height = max(30, height // 3)
        return [
            MatchPlayerCandidate(candidate_id=f"player-{index + 1}", label=f"Player {index + 1}", box=MatchBoundingBox(x=12 + index * 28, y=12, width=box_width, height=box_height), confidence=0.9)
            for index in range(4)
        ]


class ThresholdDetector:
    def detect(self, frame):  # type: ignore[override]
        mean_value = float(frame.mean())
        if mean_value < 20.0:
            return []

        height, width = frame.shape[:2]
        box_width = max(16, width // 8)
        box_height = max(24, height // 4)
        if mean_value < 80.0:
            return [
                MatchPlayerCandidate(
                    candidate_id="candidate-1",
                    label="Player 1",
                    box=MatchBoundingBox(x=width // 3, y=height // 4, width=box_width, height=box_height),
                    confidence=0.72,
                )
            ]

        return [
            MatchPlayerCandidate(
                candidate_id=f"candidate-{index + 1}",
                label=f"Player {index + 1}",
                box=MatchBoundingBox(x=12 + index * 28, y=12, width=box_width, height=box_height),
                confidence=0.9,
            )
            for index in range(4)
        ]


class FakeTracker:
    def __init__(self) -> None:
        self._box = MatchBoundingBox(x=10, y=20, width=28, height=44)
        self._frame = 0

    def initialize(self, frame, box):  # type: ignore[override]
        self._box = box
        self._frame = 0

    def update(self, frame):  # type: ignore[override]
        self._frame += 1
        moved_box = MatchBoundingBox(
            x=self._box.x + min(self._frame * 2, 18),
            y=self._box.y,
            width=self._box.width,
            height=self._box.height,
        )
        return TrackingObservation(tracked=True, box=moved_box, confidence=0.88)


class RecordingTracker:
    def __init__(self) -> None:
        self.initialized_frame_means: list[float] = []
        self._box = MatchBoundingBox(x=10, y=20, width=28, height=44)
        self._frame = 0

    def initialize(self, frame, box):  # type: ignore[override]
        self._box = box
        self.initialized_frame_means.append(float(frame.mean()))
        self._frame = 0

    def update(self, frame):  # type: ignore[override]
        self._frame += 1
        moved_box = MatchBoundingBox(
            x=self._box.x + min(self._frame * 2, 18),
            y=self._box.y,
            width=self._box.width,
            height=self._box.height,
        )
        return TrackingObservation(tracked=True, box=moved_box, confidence=0.88)


def _build_job(source: dict[str, object] | None = None) -> JobResponse:
    return JobResponse(
        job_id="job-match",
        analysis_id=101,
        status="processing",
        workflow_mode="worker",
        created_at=1.0,
        updated_at=2.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(download_url="http://example.com/video.mp4"),
        source=source or {},
    )


def _build_multi_frame_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"XVID"), 10.0, (160, 120))
    if not writer.isOpened():
        raise RuntimeError("Unable to create synthetic video.")

    for index in range(24):
        if index < 4:
            value = 0
        elif index < 12:
            value = 55
        else:
            value = 180
        frame = np.full((120, 160, 3), value, dtype=np.uint8)
        writer.write(frame)

    writer.release()


def test_match_adapter_returns_preview_when_no_selection(tmp_path: Path) -> None:
    video_path = tmp_path / "match.avi"
    build_synthetic_video(video_path, frame_count=18, size=(160, 120), motion=True)

    adapter = MatchAnalysisAdapter(
        storage_root=tmp_path / "storage",
        detector=FakeDetector(),
        tracker_factory=FakeTracker,
    )
    result = adapter.analyze(_build_job({"analysis_mode": "match"}), str(video_path))

    assert result.job_status == "awaiting_player_selection"
    assert result.match is not None
    assert result.match.stage == "selection_required"
    assert result.match.preview is not None
    assert len(result.match.preview.candidates) == 4
    assert result.match.artifacts is not None
    assert result.match.artifacts.preview_image is not None


def test_match_adapter_returns_completed_result_after_selection(tmp_path: Path) -> None:
    video_path = tmp_path / "match.avi"
    build_synthetic_video(video_path, frame_count=24, size=(160, 120), motion=True)

    adapter = MatchAnalysisAdapter(
        storage_root=tmp_path / "storage",
        detector=FakeDetector(),
        tracker_factory=FakeTracker,
    )
    preview = adapter.analyze(_build_job({"analysis_mode": "match"}), str(video_path))
    assert preview.match is not None
    assert preview.match.preview is not None
    selected_candidate_id = preview.match.preview.candidates[0].candidate_id
    result = adapter.analyze(
        _build_job({
            "analysis_mode": "match",
            "selected_player_candidate_id": selected_candidate_id,
        }),
        str(video_path),
    )

    assert result.job_status == "completed"
    assert result.match is not None
    assert result.match.stage == "completed"
    assert result.match.tracking is not None
    assert result.match.tracking.coverage > 0.5
    assert result.match.artifacts is not None
    assert result.match.artifacts.tracking_preview_image is not None


def test_match_adapter_can_pick_player_from_later_frame(tmp_path: Path) -> None:
    video_path = tmp_path / "later-frame.avi"
    _build_multi_frame_video(video_path)

    adapter = MatchAnalysisAdapter(
        storage_root=tmp_path / "storage",
        detector=ThresholdDetector(),
        tracker_factory=FakeTracker,
    )
    preview = adapter.analyze(_build_job({"analysis_mode": "match"}), str(video_path))

    assert preview.job_status == "awaiting_player_selection"
    assert preview.match is not None
    assert len(preview.match.preview.frames) >= 3
    assert preview.match.preview.candidates
    assert preview.match.preview.frame_index >= 4
    assert preview.match.preview.candidates[0].candidate_id.startswith(f"frame-{preview.match.preview.frame_index}-")

    selected_candidate_id = preview.match.preview.candidates[0].candidate_id
    completed = adapter.analyze(
        _build_job({
            "analysis_mode": "match",
            "selected_player_candidate_id": selected_candidate_id,
        }),
        str(video_path),
    )

    assert completed.job_status == "completed"
    assert completed.match is not None
    assert completed.match.tracking is not None
    assert completed.match.tracking.frames[0].frame_index == preview.match.preview.frame_index
    assert completed.match.tracking.coverage > 0.25


def test_match_adapter_initializes_tracking_on_selected_frame(tmp_path: Path) -> None:
    video_path = tmp_path / "later-frame-tracking.avi"
    _build_multi_frame_video(video_path)

    tracker = RecordingTracker()
    adapter = MatchAnalysisAdapter(
        storage_root=tmp_path / "storage",
        detector=ThresholdDetector(),
        tracker_factory=lambda: tracker,
    )
    preview = adapter.analyze(_build_job({"analysis_mode": "match"}), str(video_path))
    assert preview.match is not None
    assert preview.match.preview is not None
    selected_candidate_id = preview.match.preview.candidates[0].candidate_id

    completed = adapter.analyze(
        _build_job({
            "analysis_mode": "match",
            "selected_player_candidate_id": selected_candidate_id,
        }),
        str(video_path),
    )

    assert completed.job_status == "completed"
    assert completed.match is not None
    assert completed.match.tracking is not None
    assert completed.match.tracking.frames
    assert completed.match.tracking.frames[0].frame_index == preview.match.preview.frame_index
    assert tracker.initialized_frame_means
    assert tracker.initialized_frame_means[0] > 0.0
    assert completed.match.artifacts is not None
    assert completed.match.artifacts.tracking_preview_video is not None
