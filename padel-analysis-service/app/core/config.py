from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    service_name: str
    service_version: str
    api_secret: str
    pose_model_path: str
    stroke_smoothing_alpha: float
    db_path: Path
    storage_dir: Path
    download_timeout_seconds: float
    max_download_bytes: int
    queue_delay_seconds: float
    processing_delay_seconds: float
    pose_min_detection_confidence: float
    pose_min_presence_confidence: float
    pose_min_tracking_confidence: float


def get_settings() -> Settings:
    return Settings(
        service_name="padel-analysis-service",
        service_version="0.1.0",
        api_secret=os.getenv("PADEL_ANALYSIS_SERVICE_SECRET", ""),
        pose_model_path=os.getenv("POSE_MODEL_PATH", "/opt/mediapipe-models/pose_landmarker_lite.task"),
        stroke_smoothing_alpha=float(os.getenv("STROKE_SMOOTHING_ALPHA", "0.35")),
        db_path=Path(os.getenv("PADEL_ANALYSIS_SERVICE_DB_PATH", "/tmp/padel-analysis-service/jobs.sqlite3")),
        storage_dir=Path(os.getenv("PADEL_ANALYSIS_SERVICE_STORAGE_DIR", "/tmp/padel-analysis-service/storage")),
        download_timeout_seconds=float(os.getenv("PADEL_ANALYSIS_SERVICE_DOWNLOAD_TIMEOUT_SECONDS", "20")),
        max_download_bytes=int(os.getenv("PADEL_ANALYSIS_SERVICE_MAX_DOWNLOAD_BYTES", "78643200")),
        queue_delay_seconds=float(os.getenv("PADEL_ANALYSIS_SERVICE_QUEUE_DELAY_SECONDS", "1")),
        processing_delay_seconds=float(os.getenv("PADEL_ANALYSIS_SERVICE_PROCESSING_DELAY_SECONDS", "3")),
        pose_min_detection_confidence=float(os.getenv("POSE_MIN_DETECTION_CONFIDENCE", "0.5")),
        pose_min_presence_confidence=float(os.getenv("POSE_MIN_PRESENCE_CONFIDENCE", "0.5")),
        pose_min_tracking_confidence=float(os.getenv("POSE_MIN_TRACKING_CONFIDENCE", "0.5")),
    )
