from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.match_models import (
    MatchAnalysisSummary,
    MatchArtifactBundle,
    MatchBoundingBox,
    MatchPlayerCandidate,
    MatchPreviewSummary,
    MatchStrokeCandidate,
    MatchStrokeCandidateArtifacts,
    MatchTrackFrame,
    MatchTrackingSummary,
)
from app.models import JobArtifacts, JobResult


def _build_client(tmp_path: Path) -> TestClient:
    os.environ["PADEL_ANALYSIS_SERVICE_SECRET"] = "test-secret"
    os.environ["PADEL_ANALYSIS_SERVICE_DB_PATH"] = str(tmp_path / "jobs.sqlite3")
    os.environ["PADEL_ANALYSIS_SERVICE_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["PADEL_ANALYSIS_SERVICE_QUEUE_DELAY_SECONDS"] = "0"
    os.environ["PADEL_ANALYSIS_SERVICE_PROCESSING_DELAY_SECONDS"] = "0"
    client = TestClient(create_app())

    class FakeEngine:
        def process(self, job: dict[str, object]) -> dict[str, object]:
            selected = str(job.get("payload", {}).get("selected_player_candidate_id", ""))
            if not selected:
                return _preview_result(tmp_path).model_dump(mode="json")
            return _completed_result(tmp_path, selected).model_dump(mode="json")

    client.app.state.store._engine = FakeEngine()  # noqa: SLF001
    return client


def _artifact(storage_dir: Path, relative_path: str, content: bytes) -> str:
    path = storage_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return relative_path


def _preview_result(tmp_path: Path) -> JobResult:
    storage_dir = tmp_path / "storage"
    preview_key = _artifact(storage_dir, "match/job-match/match-preview.jpg", b"preview")
    preview = MatchPreviewSummary(
        frame_index=10,
        timestamp_ms=1000,
        width=160,
        height=120,
        candidates=[
            MatchPlayerCandidate(candidate_id="candidate-1", label="Player 1", box=MatchBoundingBox(x=10, y=10, width=24, height=42), confidence=0.9),
            MatchPlayerCandidate(candidate_id="candidate-2", label="Player 2", box=MatchBoundingBox(x=40, y=10, width=24, height=42), confidence=0.9),
            MatchPlayerCandidate(candidate_id="candidate-3", label="Player 3", box=MatchBoundingBox(x=70, y=10, width=24, height=42), confidence=0.9),
            MatchPlayerCandidate(candidate_id="candidate-4", label="Player 4", box=MatchBoundingBox(x=100, y=10, width=24, height=42), confidence=0.9),
        ],
    )
    return JobResult(
        job_status="awaiting_player_selection",
        summary="Preview ready.",
        findings=[],
        recommendations=[],
        analysis_id=101,
        job_id="job-match",
        processor="opencv_match_tracker",
        service_version="0.1.0",
        video=None,
        match=MatchAnalysisSummary(
            enabled=True,
            available=True,
            stage="selection_required",
            selection_required=True,
            preview=preview,
            artifacts=MatchArtifactBundle(
                preview_image={
                    "type": "preview",
                    "storage_key": preview_key,
                    "mime_type": "image/jpeg",
                    "filename": "match-preview.jpg",
                }
            ),
        ),
    )


def _completed_result(tmp_path: Path, selected: str) -> JobResult:
    storage_dir = tmp_path / "storage"
    preview_key = _artifact(storage_dir, "match/job-match/match-preview.jpg", b"preview")
    tracking_key = _artifact(storage_dir, "match/job-match/tracking-preview.jpg", b"tracking")
    metadata_key = _artifact(storage_dir, "match/job-match/match-metadata.json", b"{}")
    clip_key = _artifact(storage_dir, f"match/job-match/{selected}-clip.mp4", b"clip")
    thumb_key = _artifact(storage_dir, f"match/job-match/{selected}-thumbnail.jpg", b"thumb")

    preview = _preview_result(tmp_path).match.preview  # type: ignore[union-attr]
    tracking = MatchTrackingSummary(
        track_id=f"{selected}-track",
        selected_player_candidate_id=selected,
        selected_player_label="Player 1",
        coverage=0.91,
        confidence_level="high",
        total_frames=20,
        tracked_frames=18,
        frames=[
            MatchTrackFrame(frame_index=index, timestamp_ms=index * 100, tracked=True, confidence=0.9, box=MatchBoundingBox(x=10 + index, y=10, width=24, height=42))
            for index in range(20)
        ],
    )
    candidate = MatchStrokeCandidate(
        candidate_id=selected,
        start_frame=4,
        start_timestamp_ms=400,
        peak_frame=8,
        peak_timestamp_ms=800,
        end_frame=12,
        end_timestamp_ms=1200,
        confidence=0.88,
        selected_player_track_confidence=0.91,
        artifacts=MatchStrokeCandidateArtifacts(
            clip={
                "type": "clip",
                "storage_key": clip_key,
                "mime_type": "video/mp4",
                "filename": f"{selected}-clip.mp4",
            },
            thumbnail={
                "type": "thumbnail",
                "storage_key": thumb_key,
                "mime_type": "image/jpeg",
                "filename": f"{selected}-thumbnail.jpg",
            },
        ),
    )
    return JobResult(
        job_status="completed",
        summary="Completed.",
        findings=[],
        recommendations=[],
        analysis_id=101,
        job_id="job-match",
        processor="opencv_match_tracker",
        service_version="0.1.0",
        video=None,
        match=MatchAnalysisSummary(
            enabled=True,
            available=True,
            stage="completed",
            selection_required=False,
            selected_player_candidate_id=selected,
            selected_player_label="Player 1",
            preview=preview,
            tracking=tracking,
            stroke_candidates=[candidate],
            artifacts=MatchArtifactBundle(
                preview_image={
                    "type": "preview",
                    "storage_key": preview_key,
                    "mime_type": "image/jpeg",
                    "filename": "match-preview.jpg",
                },
                tracking_preview_image={
                    "type": "tracking_preview",
                    "storage_key": tracking_key,
                    "mime_type": "image/jpeg",
                    "filename": "tracking-preview.jpg",
                },
                metadata={
                    "type": "metadata",
                    "storage_key": metadata_key,
                    "mime_type": "application/json",
                    "filename": "match-metadata.json",
                },
            ),
        ),
    )


def test_match_selection_route_and_artifact_download(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 101,
            "owner_user_id": 7,
            "analysis_mode": "match",
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "side",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "match.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 30.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-match",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["job_id"]

    client.app.state.store.update_job_state(  # noqa: SLF001
        job_id,
        "awaiting_player_selection",
        result=_preview_result(tmp_path).model_dump(mode="json"),
    )

    selected = client.post(
        f"/api/v1/jobs/{job_id}/selected-player",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={"selected_player_candidate_id": "candidate-1"},
    )
    assert selected.status_code == 200
    payload = selected.json()
    assert payload["status"] == "queued"

    leased = client.app.state.store.lease_next_job()  # noqa: SLF001
    assert leased is not None
    completed_result = client.app.state.store._engine.process(leased)  # noqa: SLF001
    client.app.state.store.update_job_state(  # noqa: SLF001
        job_id,
        "completed",
        result=completed_result,
    )

    completed = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"X-Padel-API-Secret": "test-secret"},
    )
    assert completed.status_code == 200
    completed_payload = completed.json()
    assert completed_payload["status"] == "completed"
    assert completed_payload["result"]["job_status"] == "completed"
    assert completed_payload["result"]["match"]["tracking"]["coverage"] > 0.5

    artifact = client.get(
        f"/api/v1/jobs/{job_id}/artifacts/match-preview",
        headers={"X-Padel-API-Secret": "test-secret"},
    )
    assert artifact.status_code == 200
    assert artifact.headers["content-type"].startswith("image/jpeg")


def test_match_selection_accepts_completed_preview_state(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 101,
            "owner_user_id": 7,
            "analysis_mode": "match",
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "side",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "match.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 30.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-match-preview-complete",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["job_id"]

    client.app.state.store.update_job_state(  # noqa: SLF001
        job_id,
        "completed",
        result=_preview_result(tmp_path).model_dump(mode="json"),
    )

    selected = client.post(
        f"/api/v1/jobs/{job_id}/selected-player",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "selected_player_candidate_id": "candidate-1",
            "video_download_url": "http://example.com/fresh-video.mp4",
        },
    )
    assert selected.status_code == 200
    payload = selected.json()
    assert payload["status"] == "queued"
    stored = client.app.state.store.get_job(job_id)  # noqa: SLF001
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["payload"]["selected_player_candidate_id"] == "candidate-1"
    assert stored["download_url"] == "http://example.com/fresh-video.mp4"


def test_match_selection_is_idempotent_for_duplicate_confirm(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 101,
            "owner_user_id": 7,
            "analysis_mode": "match",
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "side",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "match.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 30.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-match-idempotent",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["job_id"]

    client.app.state.store.update_job_state(  # noqa: SLF001
        job_id,
        "awaiting_player_selection",
        result=_preview_result(tmp_path).model_dump(mode="json"),
    )

    first = client.post(
        f"/api/v1/jobs/{job_id}/selected-player",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "selected_player_candidate_id": "candidate-1",
            "video_download_url": "http://example.com/fresh-video.mp4",
        },
    )
    assert first.status_code == 200

    duplicate = client.post(
        f"/api/v1/jobs/{job_id}/selected-player",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "selected_player_candidate_id": "candidate-1",
            "video_download_url": "http://example.com/fresh-video-2.mp4",
        },
    )
    assert duplicate.status_code == 200
    payload = duplicate.json()
    assert payload["job_id"] == job_id
    assert payload["analysis_id"] == 101
    assert payload["status"] == "queued"

    stored = client.app.state.store.get_job(job_id)  # noqa: SLF001
    assert stored is not None
    assert stored["job_id"] == job_id
    assert stored["analysis_id"] == 101
    assert stored["payload"]["selected_player_candidate_id"] == "candidate-1"
    assert stored["download_url"] == "http://example.com/fresh-video.mp4"
