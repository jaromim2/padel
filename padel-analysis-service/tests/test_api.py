from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.analyzer_adapter import VideoAnalysisAdapter
from app.main import create_app
from app.models import JobArtifacts, JobResponse, JobResult
from app.pose_estimator import PoseProcessingError
from app.pose_models import PoseAnalysisSummary, PoseArtifactBundle, PoseArtifactReference, PoseEstimationResult, PoseVideoSummary
from app.stroke_analysis import StrokeProcessingError
from tests.helpers import build_forehand_pose_sequence, build_synthetic_video


def _build_client(tmp_path: Path, engine: object | None = None) -> TestClient:
    os.environ["PADEL_ANALYSIS_SERVICE_SECRET"] = "test-secret"
    os.environ["PADEL_ANALYSIS_SERVICE_DB_PATH"] = str(tmp_path / "jobs.sqlite3")
    os.environ["PADEL_ANALYSIS_SERVICE_STORAGE_DIR"] = str(tmp_path / "storage")
    os.environ["PADEL_ANALYSIS_SERVICE_QUEUE_DELAY_SECONDS"] = "0.25"
    os.environ["PADEL_ANALYSIS_SERVICE_PROCESSING_DELAY_SECONDS"] = "0.25"
    os.environ["PADEL_ANALYSIS_SERVICE_DOWNLOAD_TIMEOUT_SECONDS"] = "5"
    os.environ["PADEL_ANALYSIS_SERVICE_MAX_DOWNLOAD_BYTES"] = "1048576"
    client = TestClient(create_app())
    sample_video = tmp_path / "sample.avi"
    build_synthetic_video(sample_video, frame_count=12, size=(64, 64), motion=True)
    client.app.state.store._download_video = lambda job: {  # noqa: SLF001
        "path": str(sample_video),
        "size": sample_video.stat().st_size,
        "sha256": "abc123",
    }
    client.app.state.store._engine = engine or _FakeEngine()  # noqa: SLF001
    return client


def test_health_and_job_lifecycle(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready", headers={"X-Padel-API-Secret": "test-secret"}).json() == {"status": "ready"}

    response = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 1,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-1",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "queued"
    job_id = payload["job_id"]

    duplicate = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 2,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-1",
        },
    )
    assert duplicate.status_code == 201
    duplicate_job_id = duplicate.json()["job_id"]
    assert duplicate_job_id != job_id

    time.sleep(0.3)
    client.app.state.store.advance_due_jobs()  # noqa: SLF001
    payload = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Padel-API-Secret": "test-secret"}).json()
    assert payload["status"] in {"processing", "completed"}

    time.sleep(0.4)
    client.app.state.store.advance_due_jobs()  # noqa: SLF001
    payload = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Padel-API-Secret": "test-secret"}).json()
    time.sleep(0.1)
    client.app.state.store.advance_due_jobs()  # noqa: SLF001
    duplicate_payload = client.get(f"/api/v1/jobs/{duplicate_job_id}", headers={"X-Padel-API-Secret": "test-secret"}).json()
    assert payload["status"] == "completed"
    assert duplicate_payload["status"] == "completed"
    assert payload["result"]["summary"] == "Video analysis completed successfully."
    assert duplicate_payload["result"]["summary"] == "Video analysis completed successfully."
    assert payload["result"]["analysis_id"] == 1
    assert duplicate_payload["result"]["analysis_id"] == 2
    assert payload["result"]["processor"] == "opencv_heuristic_video_analyzer"
    assert payload["result"]["pose"]["confidence_level"] == "high"
    assert payload["result"]["artifacts"]["landmarks"]["storage_key"].endswith("landmarks.json")


def test_get_job_is_read_only(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 3,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-read-only",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["job_id"]

    called = {"advance": 0}

    def _advance_due_jobs() -> None:
        called["advance"] += 1
        raise AssertionError("advance_due_jobs should not be called from GET /api/v1/jobs/{job_id}")

    client.app.state.store.advance_due_jobs = _advance_due_jobs  # noqa: SLF001

    response = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Padel-API-Secret": "test-secret"})
    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert called["advance"] == 0


def test_same_video_different_users_create_independent_jobs(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    first = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 11,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "same-video",
            "submission_fingerprint": "fingerprint-user-7",
        },
    )
    second = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 12,
            "owner_user_id": 8,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "same-video",
            "submission_fingerprint": "fingerprint-user-8",
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["job_id"] != second.json()["job_id"]


def test_duplicate_deletion_does_not_remove_other_artifacts(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    first = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 21,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "dup-video",
            "submission_fingerprint": "fingerprint-dup",
        },
    )
    second = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 22,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "dup-video",
            "submission_fingerprint": "fingerprint-dup",
        },
    )

    first_job_id = first.json()["job_id"]
    second_job_id = second.json()["job_id"]

    time.sleep(0.8)
    client.app.state.store.advance_due_jobs()  # noqa: SLF001
    first_payload = client.get(f"/api/v1/jobs/{first_job_id}", headers={"X-Padel-API-Secret": "test-secret"}).json()
    time.sleep(0.1)
    client.app.state.store.advance_due_jobs()  # noqa: SLF001
    second_payload = client.get(f"/api/v1/jobs/{second_job_id}", headers={"X-Padel-API-Secret": "test-secret"}).json()
    assert first_payload["status"] == "completed"
    assert second_payload["status"] == "completed"

    first_landmarks = tmp_path / "storage" / first_payload["result"]["artifacts"]["landmarks"]["storage_key"]
    second_landmarks = tmp_path / "storage" / second_payload["result"]["artifacts"]["landmarks"]["storage_key"]
    first_landmarks.parent.mkdir(parents=True, exist_ok=True)
    second_landmarks.parent.mkdir(parents=True, exist_ok=True)
    first_landmarks.write_text("{}", encoding="utf-8")
    second_landmarks.write_text("{}", encoding="utf-8")

    deleted = client.delete(f"/api/v1/jobs/{first_job_id}", headers={"X-Padel-API-Secret": "test-secret"})
    assert deleted.status_code == 200
    assert second_landmarks.exists()


def test_auth_rejected(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    response = client.get("/health/ready")
    assert response.status_code == 401


def test_status_callback_completes_job(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 2,
            "owner_user_id": 8,
            "shot_type": "forehand",
            "dominant_hand": "left",
            "camera_angle": "baseline",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 18.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-2",
        },
    )
    job_id = created.json()["job_id"]

    update = client.post(
        f"/api/v1/jobs/{job_id}/status",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "status": "completed",
            "result": {
                "summary": "External analyzer completed.",
                "findings": ["Mock finding"],
                "recommendations": ["Mock recommendation"],
                "analysis_id": 2,
                "job_id": job_id,
                "processor": "external_python_analyzer",
                "service_version": "1.0.0",
            },
        },
    )
    assert update.status_code == 200
    payload = update.json()
    assert payload["status"] == "completed"
    assert payload["result"]["processor"] == "external_python_analyzer"


def test_lease_next_job_claims_job(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 3,
            "owner_user_id": 9,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "baseline",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 14.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-3",
        },
    )
    assert created.status_code == 201

    time.sleep(0.3)
    leased = client.post("/api/v1/jobs/lease-next", headers={"X-Padel-API-Secret": "test-secret"})
    assert leased.status_code == 200
    payload = leased.json()
    assert payload["status"] == "processing"
    assert payload["workflow_mode"] == "worker"


def test_job_result_without_pose_fields_remains_readable() -> None:
    result = JobResult.model_validate(
        {
            "summary": "Legacy completed job.",
            "findings": ["legacy"],
            "recommendations": ["legacy"],
            "analysis_id": 11,
            "job_id": "job-legacy",
            "processor": "mock_python_service",
            "service_version": "1.0.0",
        }
    )

    assert result.pose is None
    assert result.stroke is None
    assert result.artifacts is None
    assert result.video is None


def test_processing_failure_marks_job_failed(tmp_path: Path) -> None:
    class FailingEngine:
        def process(self, job: dict[str, object]) -> dict[str, object]:
            raise PoseProcessingError(
                code="pose_artifact_write_failed",
                message="Unable to write pose artifacts.",
                detail={"stage": "artifact_write"},
            )

    client = _build_client(tmp_path, engine=FailingEngine())
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 4,
            "owner_user_id": 10,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-4",
        },
    )
    job_id = created.json()["job_id"]

    for _ in range(10):
        client.app.state.store.advance_due_jobs()  # noqa: SLF001
        response = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Padel-API-Secret": "test-secret"})
        payload = response.json()
        if payload["status"] == "failed":
            break
        time.sleep(0.1)

    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "pose_artifact_write_failed"


def test_stroke_processing_failure_marks_job_failed(tmp_path: Path) -> None:
    class FailingEngine:
        def process(self, job: dict[str, object]) -> dict[str, object]:
            raise StrokeProcessingError(
                code="stroke_processing_failed",
                message="Stroke analysis failed.",
                detail={"stage": "contact_detection"},
            )

    client = _build_client(tmp_path, engine=FailingEngine())
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 5,
            "owner_user_id": 11,
            "shot_type": "forehand",
            "dominant_hand": "left",
            "camera_angle": "rear",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 12.0,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-5",
        },
    )
    job_id = created.json()["job_id"]

    for _ in range(10):
        client.app.state.store.advance_due_jobs()  # noqa: SLF001
        response = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Padel-API-Secret": "test-secret"})
        payload = response.json()
        if payload["status"] == "failed":
            break
        time.sleep(0.1)

    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "stroke_processing_failed"


def test_contact_frame_update_recomputes_from_stored_landmarks(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    analysis_id = 6
    video_path = tmp_path / "manual.avi"
    build_synthetic_video(video_path, frame_count=24, size=(64, 64), motion=True)

    pose_result = PoseEstimationResult(
        video_path=str(video_path),
        model_name="fake",
        model_path="/fake/model.task",
        total_frames=24,
        processed_frames=24,
        frame_rate=10.0,
        width=64,
        height=64,
        duration_seconds=2.4,
        rotation_normalized=False,
        frames=build_forehand_pose_sequence(handedness="right", frame_count=24, visibility=0.92),
    )
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": analysis_id,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "side",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 2.4,
            "video_sha256": "abc123",
            "submission_fingerprint": f"fingerprint-{analysis_id}",
        },
    )
    job_id = created.json()["job_id"]
    class FakePoseEstimator:
        def estimate(self, path: str) -> PoseEstimationResult:
            return pose_result.model_copy(update={"video_path": path})

    job = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator()).analyze(
        _build_job_response(job_id, analysis_id, dominant_hand="right"),
        str(video_path),
    )
    client.app.state.store.update_job_state(job_id, "completed", result=job.model_dump(mode="json"))  # noqa: SLF001

    original_contact = job.stroke.phases.contact_estimate.frame_index
    manual_frame = original_contact + 1
    response = client.patch(
        f"/api/v1/jobs/{job_id}/contact-frame",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={"frame_index": manual_frame},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["result"]["stroke"]["phases"]["original_contact_estimate"]["frame_index"] == original_contact
    assert payload["result"]["stroke"]["phases"]["contact_estimate"]["frame_index"] == manual_frame
    assert payload["result"]["stroke"]["phases"]["contact_estimate"]["source"] == "manual"
    assert payload["result"]["stroke"]["phases"]["recovery"] is None or "insufficient_post_follow_through_footage" in payload["result"]["stroke"]["reasons"]


def test_contact_frame_update_rejects_invalid_frame(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    video_path = tmp_path / "invalid.avi"
    build_synthetic_video(video_path, frame_count=24, size=(64, 64), motion=True)
    pose_result = PoseEstimationResult(
        video_path=str(video_path),
        model_name="fake",
        model_path="/fake/model.task",
        total_frames=24,
        processed_frames=24,
        frame_rate=10.0,
        width=64,
        height=64,
        duration_seconds=2.4,
        rotation_normalized=False,
        frames=build_forehand_pose_sequence(handedness="right", frame_count=24, visibility=0.92),
    )
    created = client.post(
        "/api/v1/jobs",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={
            "analysis_id": 7,
            "owner_user_id": 7,
            "shot_type": "forehand",
            "dominant_hand": "right",
            "camera_angle": "side",
            "video_download_url": "http://example.com/video.mp4",
            "video_name": "video.mp4",
            "video_mime_type": "video/mp4",
            "video_size": 1234,
            "video_duration_seconds": 2.4,
            "video_sha256": "abc123",
            "submission_fingerprint": "fingerprint-7",
        },
    )
    job_id = created.json()["job_id"]
    class FakePoseEstimator:
        def estimate(self, path: str) -> PoseEstimationResult:
            return pose_result.model_copy(update={"video_path": path})

    job = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator()).analyze(
        _build_job_response(job_id, 7),
        str(video_path),
    )
    client.app.state.store.update_job_state(job_id, "completed", result=job.model_dump(mode="json"))  # noqa: SLF001

    response = client.patch(
        f"/api/v1/jobs/{job_id}/contact-frame",
        headers={"X-Padel-API-Secret": "test-secret"},
        json={"frame_index": 999},
    )
    assert response.status_code == 400


def test_short_post_follow_through_footage_marks_recovery_unavailable(tmp_path: Path) -> None:
    video_path = tmp_path / "short.avi"
    build_synthetic_video(video_path, frame_count=18, size=(64, 64), motion=True)

    pose_result = PoseEstimationResult(
        video_path=str(video_path),
        model_name="fake",
        model_path="/fake/model.task",
        total_frames=18,
        processed_frames=18,
        frame_rate=10.0,
        width=64,
        height=64,
        duration_seconds=1.8,
        rotation_normalized=False,
        frames=build_forehand_pose_sequence(handedness="right", frame_count=18, visibility=0.92),
    )
    class FakePoseEstimator:
        def estimate(self, path: str) -> PoseEstimationResult:
            return pose_result.model_copy(update={"video_path": path})

    result = VideoAnalysisAdapter(storage_root=tmp_path / "storage", pose_estimator=FakePoseEstimator()).analyze(
        _build_job_response("job-short", 8),
        str(video_path),
    )

    assert result.stroke is not None
    assert result.stroke.phases.recovery is None
    assert "insufficient_post_follow_through_footage" in result.stroke.reasons
    assert result.stroke.metrics is not None
    assert result.stroke.metrics.recovery_duration_ms is None


class _FakeEngine:
    def process(self, job: dict[str, object]) -> dict[str, object]:
        job_id = str(job["job_id"])
        return JobResult(
            summary="Video analysis completed successfully.",
            findings=["pose landmarks extracted"],
            recommendations=["keep capture stable"],
            analysis_id=int(job["analysis_id"]),
            job_id=job_id,
            processor="opencv_heuristic_video_analyzer",
            service_version="1.0.0",
            video=PoseVideoSummary(
                total_frames=12,
                processed_frames=12,
                frame_rate=10.0,
                width=64,
                height=64,
                duration_seconds=1.2,
                motion_score=3.1,
                motion_label="stable",
                rotation_normalized=False,
            ),
            pose=PoseAnalysisSummary(
                confidence_level="high",
                pose_coverage=1.0,
                full_body_visible=True,
                total_frames=12,
                processed_frames=12,
                frames_with_pose=12,
            ),
            artifacts=PoseArtifactBundle(
                landmarks=PoseArtifactReference(
                    type="json",
                    storage_key=f"pose/{job_id}/landmarks.json",
                    mime_type="application/json",
                    filename="landmarks.json",
                ),
                metadata=PoseArtifactReference(
                    type="json",
                    storage_key=f"pose/{job_id}/pose-metadata.json",
                    mime_type="application/json",
                    filename="pose-metadata.json",
                ),
                annotated_video=PoseArtifactReference(
                    type="video",
                    storage_key=f"pose/{job_id}/annotated.mp4",
                    mime_type="video/mp4",
                    filename="annotated.mp4",
                ),
                annotated_keyframes=[],
            ),
        ).model_dump(mode="json")


def _build_job_response(job_id: str, analysis_id: int, *, dominant_hand: str = "right", shot_type: str = "forehand") -> JobResponse:
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
