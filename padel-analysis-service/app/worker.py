from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
import tempfile
from urllib.parse import urlparse

import httpx

from .models import JobArtifacts, JobError, JobResponse, JobResult, JobStatusUpdateRequest
from .worker_processor import RealWorkerProcessor, WorkerProcessor


@dataclass(slots=True)
class WorkerConfig:
    service_url: str
    api_secret: str
    timeout_seconds: float = 12.0
    poll_interval_seconds: float = 2.0


class JobWorker:
    def __init__(
        self,
        config: WorkerConfig,
        client: httpx.Client | None = None,
        processor: WorkerProcessor | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.Client(base_url=config.service_url, timeout=config.timeout_seconds)
        self._processor = processor or RealWorkerProcessor()

    def fetch_job(self, job_id: str) -> JobResponse:
        response = self._client.get(
            f"/api/v1/jobs/{job_id}",
            headers={"X-Padel-API-Secret": self._config.api_secret},
        )
        response.raise_for_status()
        return JobResponse.model_validate(response.json())

    def lease_next_job(self) -> JobResponse | None:
        response = self._client.post(
            "/api/v1/jobs/lease-next",
            headers={"X-Padel-API-Secret": self._config.api_secret},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return JobResponse.model_validate(response.json())

    def _download_job_video(self, job: JobResponse) -> tuple[str, JobArtifacts]:
        download_url = str(job.external_artifacts.download_url or "").strip()
        if not download_url:
            raise ValueError("Job is missing a download URL.")

        parsed = urlparse(download_url)
        suffix = Path(parsed.path).suffix or ".mp4"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_file.close()

        sha256 = hashlib.sha256()
        total_size = 0
        try:
            with self._client.stream(
                "GET",
                download_url,
                headers={"X-Padel-API-Secret": self._config.api_secret},
            ) as response, open(temp_file.name, "wb") as handle:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    sha256.update(chunk)
                    total_size += len(chunk)
        except Exception:
            Path(temp_file.name).unlink(missing_ok=True)
            raise

        artifacts = JobArtifacts(
            download_url=download_url,
            downloaded_video_path=temp_file.name,
            downloaded_video_size=total_size,
            downloaded_video_sha256=sha256.hexdigest(),
        )
        return temp_file.name, artifacts

    def update_job_status(
        self,
        job_id: str,
        status: str,
        *,
        result: JobResult | None = None,
        error: JobError | None = None,
        external_artifacts: JobArtifacts | None = None,
    ) -> JobResponse:
        payload = JobStatusUpdateRequest(
            status=status,
            result=result,
            error=error,
            external_artifacts=external_artifacts,
        ).model_dump(mode="json", exclude_none=True)
        response = self._client.post(
            f"/api/v1/jobs/{job_id}/status",
            headers={"X-Padel-API-Secret": self._config.api_secret},
            json=payload,
        )
        response.raise_for_status()
        return JobResponse.model_validate(response.json())

    def _build_failure_error(self, exc: Exception, stage: str) -> JobError:
        return JobError(
            code="worker_processing_failed",
            message=str(exc) or "Worker processing failed.",
            detail={
                "stage": stage,
                "type": exc.__class__.__name__,
            },
        )

    def _mark_job_failed(self, job_id: str, error: JobError, external_artifacts: JobArtifacts | None = None) -> JobResponse:
        return self.update_job_status(
            job_id,
            "failed",
            error=error,
            external_artifacts=external_artifacts,
        )

    def process_job(self, job_id: str) -> JobResponse:
        job = self.fetch_job(job_id)
        try:
            self.update_job_status(job_id, "processing")
            video_path, artifacts = self._download_job_video(job)
            try:
                result = self._processor.build_result(job, video_path)
            finally:
                Path(video_path).unlink(missing_ok=True)
            return self.update_job_status(
                job_id,
                "completed",
                result=result,
                external_artifacts=artifacts,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_job_failed(job_id, self._build_failure_error(exc, "process_job"), job.external_artifacts)
            raise

    def process_leased_job(self, job: JobResponse) -> JobResponse:
        try:
            video_path, artifacts = self._download_job_video(job)
            try:
                result = self._processor.build_result(job, video_path)
            finally:
                Path(video_path).unlink(missing_ok=True)
            return self.update_job_status(
                job.job_id,
                "completed",
                result=result,
                external_artifacts=artifacts,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_job_failed(job.job_id, self._build_failure_error(exc, "process_leased_job"), job.external_artifacts)
            raise

    def run_once(self) -> bool:
        job = self.lease_next_job()
        if job is None:
            return False
        self.process_leased_job(job)
        return True

    def run_poll_loop(self, sleep_seconds: float = 2.0) -> None:
        while True:
            try:
                did_work = self.run_once()
            except Exception:  # noqa: BLE001
                time.sleep(sleep_seconds)
                did_work = True
            if not did_work:
                time.sleep(sleep_seconds)


def build_config() -> WorkerConfig:
    return WorkerConfig(
        service_url=os.getenv("PADEL_ANALYSIS_SERVICE_URL", "http://127.0.0.1:8010").rstrip("/"),
        api_secret=os.getenv("PADEL_ANALYSIS_SERVICE_SECRET", ""),
        timeout_seconds=float(os.getenv("PADEL_ANALYSIS_WORKER_TIMEOUT_SECONDS", "12")),
        poll_interval_seconds=float(os.getenv("PADEL_ANALYSIS_WORKER_POLL_INTERVAL_SECONDS", "2")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Padel analysis worker stub")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job-id", help="Job ID to process")
    group.add_argument("--poll", action="store_true", help="Poll for queued jobs and process them continuously")
    parser.add_argument("--interval", type=float, default=None, help="Polling interval in seconds")
    args = parser.parse_args(argv)

    config = build_config()
    if not config.api_secret:
        raise SystemExit("PADEL_ANALYSIS_SERVICE_SECRET is required.")

    worker = JobWorker(config)
    if args.poll:
        interval = args.interval if args.interval is not None else config.poll_interval_seconds
        worker.run_poll_loop(interval)
        return 0

    worker.process_job(args.job_id)
    print(json.dumps({"success": True, "job_id": args.job_id}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
