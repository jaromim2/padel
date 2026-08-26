from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..models import JobArtifacts, JobResponse, JobResult
from ..match_adapter import MatchAnalysisError
from ..pose_models import PoseAnalysisSummary, PoseEstimationResult
from .analysis_engine import AnalysisEngine, RealAnalysisEngine
from .config import Settings
from ..pose_estimator import PoseProcessingError
from ..stroke_analysis import ForehandStrokeAnalyzer, StrokeAnalysisConfig, StrokeProcessingError


class JobStore:
    def __init__(self, settings: Settings, engine: AnalysisEngine | None = None) -> None:
        self._settings = settings
        self._engine = engine or RealAnalysisEngine(settings)
        self._lock = threading.Lock()
        self._settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings.storage_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._settings.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    analysis_id INTEGER NOT NULL,
                    owner_user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    workflow_mode TEXT NOT NULL DEFAULT 'auto',
                    analysis_mode TEXT NOT NULL DEFAULT 'stroke',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    ready_at REAL NOT NULL,
                    processing_at REAL NOT NULL,
                    completed_at REAL NOT NULL,
                    submission_fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_json TEXT NOT NULL DEFAULT '{}',
                    download_url TEXT NOT NULL,
                    video_name TEXT NOT NULL,
                    video_mime_type TEXT NOT NULL,
                    video_size INTEGER NOT NULL,
                    video_duration_seconds REAL NOT NULL,
                    video_sha256 TEXT NOT NULL,
                    downloaded_video_path TEXT NOT NULL DEFAULT '',
                    downloaded_video_size INTEGER NOT NULL DEFAULT 0,
                    downloaded_video_sha256 TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "workflow_mode" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN workflow_mode TEXT NOT NULL DEFAULT 'auto'")
            if "analysis_mode" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'stroke'")
            self._migrate_jobs_table_if_needed(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_analysis_id ON jobs(analysis_id)")

    def create_job(self, payload: dict[str, object]) -> dict[str, object]:
        fingerprint = str(payload["submission_fingerprint"])
        now = time.time()
        job_id = hashlib.sha256(f"{fingerprint}:{now}".encode("utf-8")).hexdigest()[:24]
        job = {
            "job_id": job_id,
            "analysis_id": int(payload["analysis_id"]),
            "owner_user_id": int(payload["owner_user_id"]),
            "status": "queued",
            "workflow_mode": "auto",
            "analysis_mode": str(payload.get("analysis_mode", "stroke")),
            "created_at": now,
            "updated_at": now,
            "ready_at": now + self._settings.queue_delay_seconds,
            "processing_at": now + self._settings.queue_delay_seconds,
            "completed_at": now + self._settings.queue_delay_seconds + self._settings.processing_delay_seconds,
            "submission_fingerprint": fingerprint,
            "payload_json": json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True),
            "result_json": "{}",
            "error_json": "{}",
            "download_url": str(payload["video_download_url"]),
            "video_name": str(payload.get("video_name", "")),
            "video_mime_type": str(payload.get("video_mime_type", "")),
            "video_size": int(payload.get("video_size", 0)),
            "video_duration_seconds": float(payload.get("video_duration_seconds", 0)),
            "video_sha256": str(payload.get("video_sha256", "")),
            "downloaded_video_path": "",
            "downloaded_video_size": 0,
            "downloaded_video_sha256": "",
        }

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, analysis_id, owner_user_id, status, created_at, updated_at,
                    workflow_mode, analysis_mode, ready_at, processing_at, completed_at, submission_fingerprint,
                    payload_json, result_json, error_json, download_url, video_name,
                    video_mime_type, video_size, video_duration_seconds, video_sha256,
                    downloaded_video_path, downloaded_video_size, downloaded_video_sha256
                ) VALUES (
                    :job_id, :analysis_id, :owner_user_id, :status, :created_at, :updated_at,
                    :workflow_mode, :analysis_mode,
                    :ready_at, :processing_at, :completed_at, :submission_fingerprint,
                    :payload_json, :result_json, :error_json, :download_url, :video_name,
                    :video_mime_type, :video_size, :video_duration_seconds, :video_sha256,
                    :downloaded_video_path, :downloaded_video_size, :downloaded_video_sha256
                )
                """,
                job,
            )
        return self.get_job(job_id) or job

    def get_job(self, job_id: str) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_job_by_fingerprint(self, fingerprint: str) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE submission_fingerprint = ? ORDER BY created_at DESC, updated_at DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def lease_next_job(self) -> dict[str, object] | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                  AND workflow_mode = 'auto'
                  AND ready_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    workflow_mode = 'worker',
                    updated_at = ?,
                    processing_at = ?,
                    completed_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (
                    now,
                    now,
                    now + 86400,
                    row["job_id"],
                ),
            )

        return self.get_job(str(row["job_id"]))

    def update_job_state(
        self,
        job_id: str,
        status: str,
        result: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
        external_artifacts: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None

            job = self._row_to_dict(row)
            if job is None:
                return None

            now = time.time()
            payload = self._payload_from_job(job)
            result_json = json.dumps(job["result"], separators=(",", ":"), sort_keys=True) if isinstance(job.get("result"), dict) else row["result_json"]
            error_json = json.dumps(job["error"], separators=(",", ":"), sort_keys=True) if isinstance(job.get("error"), dict) else row["error_json"]
            downloaded_path = str(job.get("downloaded_video_path", ""))
            downloaded_size = int(job.get("downloaded_video_size", 0))
            downloaded_sha256 = str(job.get("downloaded_video_sha256", ""))

            if external_artifacts:
                downloaded_path = str(external_artifacts.get("downloaded_video_path", downloaded_path))
                downloaded_size = int(external_artifacts.get("downloaded_video_size", downloaded_size))
                downloaded_sha256 = str(external_artifacts.get("downloaded_video_sha256", downloaded_sha256))
                payload["external_artifacts"] = external_artifacts

            if result is not None:
                normalized_result = self._normalize_result_identity(job, result)
                result_json = json.dumps(normalized_result, separators=(",", ":"), sort_keys=True)
                payload["result"] = normalized_result

            if error is not None:
                error_json = json.dumps(error, separators=(",", ":"), sort_keys=True)
                payload["external_error"] = error

            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    updated_at = ?,
                    result_json = ?,
                    error_json = ?,
                    payload_json = ?,
                    downloaded_video_path = ?,
                    downloaded_video_size = ?,
                    downloaded_video_sha256 = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    now,
                    result_json,
                    error_json,
                    json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True),
                    downloaded_path,
                    downloaded_size,
                    downloaded_sha256,
                    job_id,
                ),
            )

        return self.get_job(job_id)

    def update_contact_frame(self, job_id: str, frame_index: int) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None

            job = self._row_to_dict(row)
            if job is None:
                return None

        if str(job["status"]) != "completed" or not isinstance(job.get("result"), dict):
            raise ValueError("contact_frame_update_requires_completed_job")

        pose_result, pose_summary = self._load_pose_artifacts(job)
        if frame_index not in {frame.frame_index for frame in pose_result.frames}:
            raise ValueError("contact_frame_out_of_range")
        analysis = JobResponse(
            job_id=str(job["job_id"]),
            analysis_id=int(job["analysis_id"]),
            status=str(job["status"]),
            workflow_mode=str(job["workflow_mode"]),
            created_at=float(job["created_at"]),
            updated_at=float(job["updated_at"]),
            result=JobResult.model_validate(job["result"]),
            error=None,
            external_artifacts=JobArtifacts(
                download_url=str(job["download_url"]),
                downloaded_video_path=str(job.get("downloaded_video_path", "")),
                downloaded_video_size=int(job.get("downloaded_video_size", 0)),
                downloaded_video_sha256=str(job.get("downloaded_video_sha256", "")),
            ),
            source=dict(job.get("payload", {})),
        )

        analyzer = ForehandStrokeAnalyzer(
            StrokeAnalysisConfig(smoothing_alpha=self._settings.stroke_smoothing_alpha)
        )
        stroke_result = analyzer.analyze(
            analysis,
            pose_result,
            pose_summary,
            manual_contact_frame_index=frame_index,
        )
        updated_result = JobResult.model_validate(job["result"]).model_copy(update={"stroke": stroke_result})
        self.update_job_state(
            job_id,
            "completed",
            result=updated_result.model_dump(mode="json"),
        )

        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT downloaded_video_path FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return False
            path = row["downloaded_video_path"]
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self._delete_tree(self._settings.storage_dir / "pose" / job_id)
        self._delete_tree(self._settings.storage_dir / "match" / job_id)
        return True

    def advance_due_jobs(self) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE workflow_mode = 'auto' AND status IN ('queued', 'processing') ORDER BY created_at ASC"
            ).fetchall()
            for row in rows:
                job = self._row_to_dict(row)
                if job is None:
                    continue
                payload = self._payload_from_job(job)
                if str(job["analysis_mode"]) == "match" and payload.get("selected_player_candidate_id"):
                    continue
                if job["status"] == "queued" and now >= float(job["ready_at"]):
                    self._transition_to_processing(conn, job, now)
                    job = self._row_to_dict(
                        conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job["job_id"],)).fetchone()
                    )
                if job and job["status"] == "processing" and now >= float(job["completed_at"]):
                    self._transition_to_completed(conn, job, now)

    def _transition_to_processing(self, conn: sqlite3.Connection, job: dict[str, object], now: float) -> None:
        downloaded = self._download_video(job)
        if isinstance(downloaded, dict) and "path" in downloaded:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    updated_at = ?,
                    payload_json = ?,
                    downloaded_video_path = ?,
                    downloaded_video_size = ?,
                    downloaded_video_sha256 = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    json.dumps({**self._payload_from_job(job), "downloaded_video": downloaded}, default=str, separators=(",", ":"), sort_keys=True),
                    downloaded["path"],
                    downloaded["size"],
                    downloaded["sha256"],
                    job["job_id"],
                ),
            )
            return

        error = downloaded if isinstance(downloaded, dict) else {
            "code": "download_failed",
            "message": "Failed to download video.",
            "detail": "",
        }
        conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                updated_at = ?,
                error_json = ?,
                payload_json = ?
            WHERE job_id = ?
            """,
            (
                now,
                json.dumps(error, separators=(",", ":"), sort_keys=True),
                json.dumps({**self._payload_from_job(job), "external_error": error}, default=str, separators=(",", ":"), sort_keys=True),
                job["job_id"],
            ),
        )

    def _transition_to_completed(self, conn: sqlite3.Connection, job: dict[str, object], now: float) -> None:
        try:
            result = self._engine.process(job)
        except (PoseProcessingError, StrokeProcessingError, MatchAnalysisError) as exc:
            error = {
                "code": getattr(exc, "code", "analysis_failed"),
                "message": getattr(exc, "message", str(exc) or "Analysis failed."),
                "detail": getattr(exc, "detail", {}) or {},
            }
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    updated_at = ?,
                    error_json = ?,
                    payload_json = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    json.dumps(error, separators=(",", ":"), sort_keys=True),
                    json.dumps({**self._payload_from_job(job), "external_error": error}, separators=(",", ":"), sort_keys=True),
                    job["job_id"],
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001
            error = {
                "code": "pose_processing_failed",
                "message": str(exc) or "Pose processing failed.",
                "detail": {"type": exc.__class__.__name__},
            }
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    updated_at = ?,
                    error_json = ?,
                    payload_json = ?
                WHERE job_id = ?
                """,
                (
                    now,
                    json.dumps(error, separators=(",", ":"), sort_keys=True),
                    json.dumps({**self._payload_from_job(job), "external_error": error}, separators=(",", ":"), sort_keys=True),
                    job["job_id"],
                ),
            )
            return

        target_status = str(result.get("job_status", "completed")) if isinstance(result, dict) else "completed"
        if target_status not in {"awaiting_player_selection", "completed", "failed"}:
            target_status = "completed"

        normalized_result = self._normalize_result_identity(job, result) if isinstance(result, dict) else result

        conn.execute(
            """
            UPDATE jobs
            SET status = ?,
                updated_at = ?,
                result_json = ?,
                payload_json = ?
            WHERE job_id = ?
            """,
            (
                target_status,
                now,
                json.dumps(normalized_result, separators=(",", ":"), sort_keys=True),
                json.dumps({**self._payload_from_job(job), "result": normalized_result}, separators=(",", ":"), sort_keys=True),
                job["job_id"],
            ),
        )

    def complete_selected_player_job(
        self,
        job_id: str,
        selected_candidate_id: str,
        download_url: str | None = None,
    ) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None

            job = self._row_to_dict(row)
            if job is None:
                return None

            if str(job["analysis_mode"]) != "match":
                raise ValueError("selected_player_supported_for_match_jobs_only")

            payload = self._payload_from_job(job)
            existing_selected_candidate_id = str(payload.get("selected_player_candidate_id", "")).strip()
            if existing_selected_candidate_id and existing_selected_candidate_id == selected_candidate_id:
                return job

            accepts_completed_preview = False
            if str(job["status"]) == "completed":
                result = job.get("result")
                match = result.get("match") if isinstance(result, dict) else None
                if not isinstance(result, dict) or not isinstance(match, dict):
                    return self.get_job(job_id)
                if str(result.get("job_status", "")) != "awaiting_player_selection":
                    return self.get_job(job_id)
                if not bool(match.get("selection_required", False)):
                    return self.get_job(job_id)
                accepts_completed_preview = True

            if str(job["status"]) not in {"awaiting_player_selection", "processing"} and not accepts_completed_preview:
                raise ValueError("selected_player_requires_preview_stage")

            if not selected_candidate_id:
                raise ValueError("selected_player_candidate_id_missing")

            payload["selected_player_candidate_id"] = selected_candidate_id
            if download_url:
                payload["video_download_url"] = download_url
                payload.setdefault("external_artifacts", {})
                if isinstance(payload.get("external_artifacts"), dict):
                    payload["external_artifacts"]["download_url"] = download_url
            if download_url:
                job["download_url"] = download_url
            job["payload"] = payload
            job["payload_json"] = json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)

            now = time.time()
            conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    updated_at = ?,
                    workflow_mode = ?,
                    ready_at = ?,
                    processing_at = ?,
                    completed_at = ?,
                    download_url = ?,
                    payload_json = ?
                WHERE job_id = ?
                """,
                (
                    "queued",
                    now,
                    "auto",
                    now,
                    now,
                    now + 86400,
                    download_url or str(job["download_url"]),
                    json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True),
                    job_id,
                ),
            )

        return self.get_job(job_id)

    def _download_video(self, job: dict[str, object]) -> dict[str, object] | dict[str, str]:
        request = Request(
            str(job["download_url"]),
            headers={"X-Padel-API-Secret": self._settings.api_secret},
            method="GET",
        )
        digest = hashlib.sha256()
        total_size = 0
        target = self._settings.storage_dir / f"{job['job_id']}.mp4"
        try:
            with urlopen(request, timeout=self._settings.download_timeout_seconds) as response, target.open("wb") as handle:  # noqa: S310
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > self._settings.max_download_bytes:
                        raise ValueError("download_too_large")
                    digest.update(chunk)
                    handle.write(chunk)
        except HTTPError as exc:
            return {
                "code": "download_failed",
                "message": f"Failed to download video: HTTP {exc.code}",
                "detail": exc.read().decode("utf-8", "ignore")[:1000],
            }
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            return {
                "code": "download_failed",
                "message": f"Failed to download video: {exc}",
                "detail": "",
            }

        return {
            "path": str(target),
            "size": total_size,
            "sha256": digest.hexdigest(),
        }

    def _payload_from_job(self, job: dict[str, object]) -> dict[str, object]:
        raw_payload = job.get("payload_json", job.get("payload", {}))
        if isinstance(raw_payload, dict):
            payload = raw_payload
        else:
            payload = json.loads(str(raw_payload))
        if isinstance(payload, dict):
            return payload
        return {}

    def _load_pose_artifacts(self, job: dict[str, object]) -> tuple[PoseEstimationResult, PoseAnalysisSummary]:
        result = job.get("result")
        if not isinstance(result, dict):
            raise ValueError("completed job result missing")

        artifacts = result.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("pose artifacts missing")

        landmarks = artifacts.get("landmarks")
        if not isinstance(landmarks, dict):
            raise ValueError("landmarks artifact missing")

        storage_key = str(landmarks.get("storage_key", ""))
        if storage_key == "":
            raise ValueError("landmarks storage key missing")

        path = (self._settings.storage_dir / storage_key).resolve()
        storage_root = self._settings.storage_dir.resolve()
        if storage_root not in path.parents and path != storage_root:
            raise ValueError("invalid artifact storage path")
        if not path.exists():
            raise ValueError("landmarks artifact not found")

        payload = json.loads(path.read_text(encoding="utf-8"))
        pose_summary = PoseAnalysisSummary.model_validate(payload.get("summary", {}))
        pose_result = PoseEstimationResult.model_validate(
            {
                **payload.get("pose", {}),
                "frames": payload.get("frames", []),
            }
        )
        return pose_result, pose_summary

    def _delete_tree(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_file():
            path.unlink(missing_ok=True)
            return
        for child in path.iterdir():
            if child.is_dir():
                self._delete_tree(child)
            else:
                child.unlink(missing_ok=True)
        try:
            path.rmdir()
        except OSError:
            pass

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, object] | None:
        if row is None:
            return None

        payload_json = json.loads(row["payload_json"])
        result_json = json.loads(row["result_json"])
        error_json = json.loads(row["error_json"])
        return {
            "job_id": row["job_id"],
            "analysis_id": row["analysis_id"],
            "owner_user_id": row["owner_user_id"],
            "status": row["status"],
            "workflow_mode": row["workflow_mode"],
            "analysis_mode": row["analysis_mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "ready_at": row["ready_at"],
            "processing_at": row["processing_at"],
            "completed_at": row["completed_at"],
            "submission_fingerprint": row["submission_fingerprint"],
            "payload_json": row["payload_json"],
            "payload": payload_json,
            "result": result_json,
            "error": error_json,
            "download_url": row["download_url"],
            "video_name": row["video_name"],
            "video_mime_type": row["video_mime_type"],
            "video_size": row["video_size"],
            "video_duration_seconds": row["video_duration_seconds"],
            "video_sha256": row["video_sha256"],
            "downloaded_video_path": row["downloaded_video_path"],
            "downloaded_video_size": row["downloaded_video_size"],
            "downloaded_video_sha256": row["downloaded_video_sha256"],
        }

    def _normalize_result_identity(self, job: dict[str, object], result: dict[str, object]) -> dict[str, object]:
        normalized = json.loads(json.dumps(result))
        normalized["analysis_id"] = int(job["analysis_id"])
        normalized["job_id"] = str(job["job_id"])
        return normalized

    def _migrate_jobs_table_if_needed(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'").fetchone()
        sql = str(row["sql"]) if row and row["sql"] else ""
        if "submission_fingerprint TEXT NOT NULL UNIQUE" not in sql:
            return

        conn.execute("ALTER TABLE jobs RENAME TO jobs_legacy")
        conn.execute(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                analysis_id INTEGER NOT NULL,
                owner_user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                workflow_mode TEXT NOT NULL DEFAULT 'auto',
                analysis_mode TEXT NOT NULL DEFAULT 'stroke',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                ready_at REAL NOT NULL,
                processing_at REAL NOT NULL,
                completed_at REAL NOT NULL,
                submission_fingerprint TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                error_json TEXT NOT NULL DEFAULT '{}',
                download_url TEXT NOT NULL,
                video_name TEXT NOT NULL,
                video_mime_type TEXT NOT NULL,
                video_size INTEGER NOT NULL,
                video_duration_seconds REAL NOT NULL,
                video_sha256 TEXT NOT NULL,
                downloaded_video_path TEXT NOT NULL DEFAULT '',
                downloaded_video_size INTEGER NOT NULL DEFAULT 0,
                downloaded_video_sha256 TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, analysis_id, owner_user_id, status, workflow_mode, analysis_mode,
                created_at, updated_at, ready_at, processing_at, completed_at, submission_fingerprint,
                payload_json, result_json, error_json, download_url, video_name, video_mime_type,
                video_size, video_duration_seconds, video_sha256, downloaded_video_path,
                downloaded_video_size, downloaded_video_sha256
            )
            SELECT
                job_id, analysis_id, owner_user_id, status, workflow_mode, analysis_mode,
                created_at, updated_at, ready_at, processing_at, completed_at, submission_fingerprint,
                payload_json, result_json, error_json, download_url, video_name, video_mime_type,
                video_size, video_duration_seconds, video_sha256, downloaded_video_path,
                downloaded_video_size, downloaded_video_sha256
            FROM jobs_legacy
            """
        )
        conn.execute("DROP TABLE jobs_legacy")
