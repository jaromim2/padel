from __future__ import annotations

import json

import httpx

from app.worker import JobWorker, WorkerConfig
from app.worker_processor import MockWorkerProcessor


def test_worker_process_job_calls_status_flow() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body.decode("utf-8")) if body else None
        calls.append((request.method, request.url.path, payload))

        if request.method == "GET" and request.url.path == "/api/v1/jobs/job-123":
            return httpx.Response(
                200,
                json={
                    "job_id": "job-123",
                    "analysis_id": 42,
                    "status": "queued",
                    "workflow_mode": "auto",
                    "created_at": 1.0,
                    "updated_at": 1.0,
                    "result": None,
                    "error": None,
                    "external_artifacts": {
                        "download_url": "http://service/media/job-123.mp4",
                        "downloaded_video_path": "",
                        "downloaded_video_size": 0,
                        "downloaded_video_sha256": "",
                    },
                    "source": {},
                },
            )

        if request.method == "GET" and request.url.path == "/media/job-123.mp4":
            return httpx.Response(200, content=b"fake-video-bytes")

        if request.method == "POST" and request.url.path == "/api/v1/jobs/job-123/status":
            status = payload["status"]
            if status == "processing":
                return httpx.Response(
                    200,
                    json={
                        "job_id": "job-123",
                        "analysis_id": 42,
                        "status": "processing",
                        "workflow_mode": "worker",
                        "created_at": 1.0,
                        "updated_at": 2.0,
                        "result": None,
                        "error": None,
                        "external_artifacts": {
                            "download_url": "http://service/media/job-123.mp4",
                            "downloaded_video_path": "",
                            "downloaded_video_size": 0,
                            "downloaded_video_sha256": "",
                        },
                        "source": {},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "job_id": "job-123",
                    "analysis_id": 42,
                    "status": "completed",
                    "workflow_mode": "worker",
                    "created_at": 1.0,
                    "updated_at": 3.0,
                    "result": payload["result"],
                    "error": None,
                    "external_artifacts": {
                        "download_url": "http://service/media/job-123.mp4",
                        "downloaded_video_path": "/tmp/job-123.mp4",
                        "downloaded_video_size": 1234,
                        "downloaded_video_sha256": "abc123",
                    },
                    "source": {},
                },
            )

        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://service")
    worker = JobWorker(
        WorkerConfig(service_url="http://service", api_secret="test-secret"),
        client=client,
        processor=MockWorkerProcessor(),
    )

    result = worker.process_job("job-123")

    assert result.status == "completed"
    assert result.result is not None
    assert result.result.processor == "external_python_worker_stub"
    assert [call[:2] for call in calls] == [
        ("GET", "/api/v1/jobs/job-123"),
        ("POST", "/api/v1/jobs/job-123/status"),
        ("GET", "/media/job-123.mp4"),
        ("POST", "/api/v1/jobs/job-123/status"),
    ]


def test_worker_run_once_leases_and_completes_job() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body.decode("utf-8")) if body else None
        calls.append((request.method, request.url.path, payload))

        if request.method == "POST" and request.url.path == "/api/v1/jobs/lease-next":
            return httpx.Response(
                200,
                json={
                    "job_id": "job-456",
                    "analysis_id": 99,
                    "status": "processing",
                    "workflow_mode": "worker",
                    "created_at": 1.0,
                    "updated_at": 2.0,
                    "result": None,
                    "error": None,
                    "external_artifacts": {
                        "download_url": "http://service/media/job-456.mp4",
                        "downloaded_video_path": "",
                        "downloaded_video_size": 0,
                        "downloaded_video_sha256": "",
                    },
                    "source": {},
                },
            )

        if request.method == "GET" and request.url.path == "/media/job-456.mp4":
            return httpx.Response(200, content=b"fake-video-bytes")

        if request.method == "POST" and request.url.path == "/api/v1/jobs/job-456/status":
            return httpx.Response(
                200,
                json={
                    "job_id": "job-456",
                    "analysis_id": 99,
                    "status": "completed",
                    "workflow_mode": "worker",
                    "created_at": 1.0,
                    "updated_at": 3.0,
                    "result": payload["result"],
                    "error": None,
                    "external_artifacts": {
                        "download_url": "http://service/media/job-456.mp4",
                        "downloaded_video_path": "/tmp/job-456.mp4",
                        "downloaded_video_size": 999,
                        "downloaded_video_sha256": "sha456",
                    },
                    "source": {},
                },
            )

        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://service")
    worker = JobWorker(
        WorkerConfig(service_url="http://service", api_secret="test-secret"),
        client=client,
        processor=MockWorkerProcessor(),
    )

    assert worker.run_once() is True
    assert [call[:2] for call in calls] == [
        ("POST", "/api/v1/jobs/lease-next"),
        ("GET", "/media/job-456.mp4"),
        ("POST", "/api/v1/jobs/job-456/status"),
    ]


def test_worker_marks_job_failed_when_processor_raises() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    class FailingProcessor:
        def build_result(self, job, video_path):  # type: ignore[override]
            raise RuntimeError("analyzer exploded")

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body.decode("utf-8")) if body else None
        calls.append((request.method, request.url.path, payload))

        if request.method == "POST" and request.url.path == "/api/v1/jobs/lease-next":
            return httpx.Response(
                200,
                json={
                    "job_id": "job-789",
                    "analysis_id": 101,
                    "status": "processing",
                    "workflow_mode": "worker",
                    "created_at": 1.0,
                    "updated_at": 2.0,
                    "result": None,
                    "error": None,
                    "external_artifacts": {
                        "download_url": "http://service/media/job-789.mp4",
                        "downloaded_video_path": "",
                        "downloaded_video_size": 0,
                        "downloaded_video_sha256": "",
                    },
                    "source": {},
                },
            )

        if request.method == "GET" and request.url.path == "/media/job-789.mp4":
            return httpx.Response(200, content=b"fake-video-bytes")

        if request.method == "POST" and request.url.path == "/api/v1/jobs/job-789/status":
            if payload["status"] == "failed":
                return httpx.Response(
                    200,
                    json={
                        "job_id": "job-789",
                        "analysis_id": 101,
                        "status": "failed",
                        "workflow_mode": "worker",
                        "created_at": 1.0,
                        "updated_at": 3.0,
                        "result": None,
                        "error": payload["error"],
                        "external_artifacts": {
                            "download_url": "http://service/media/job-789.mp4",
                            "downloaded_video_path": "",
                            "downloaded_video_size": 0,
                            "downloaded_video_sha256": "",
                        },
                        "source": {},
                    },
                )

        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://service")
    worker = JobWorker(
        WorkerConfig(service_url="http://service", api_secret="test-secret"),
        client=client,
        processor=FailingProcessor(),
    )

    try:
        worker.run_once()
        raise AssertionError("expected the processor failure to bubble up")
    except RuntimeError as exc:
        assert "analyzer exploded" in str(exc)

    assert [call[:2] for call in calls] == [
        ("POST", "/api/v1/jobs/lease-next"),
        ("GET", "/media/job-789.mp4"),
        ("POST", "/api/v1/jobs/job-789/status"),
    ]
    assert calls[-1][2]["error"]["code"] == "worker_processing_failed"
