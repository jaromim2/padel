from __future__ import annotations

import json
from pathlib import Path

import cv2

from .pose_models import (
    POSE_LANDMARK_NAMES,
    PoseAnalysisSummary,
    PoseArtifactBundle,
    PoseArtifactReference,
    PoseEstimationResult,
    PoseFrameRecord,
    PoseLandmarkRecord,
)

POSE_CONNECTIONS: list[tuple[str, str]] = [
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"),
    ("right_hip", "right_knee"),
    ("right_knee", "right_ankle"),
]


class PoseArtifactWriter:
    def __init__(self, storage_root: Path) -> None:
        self._storage_root = storage_root
        self._storage_root.mkdir(parents=True, exist_ok=True)

    def write_bundle(
        self,
        *,
        job_id: str,
        pose_result: PoseEstimationResult,
        pose_summary: PoseAnalysisSummary,
        video_path: str,
    ) -> PoseArtifactBundle:
        job_root = self._ensure_job_root(job_id)
        landmarks = self._write_landmarks_json(job_root, pose_result, pose_summary)
        metadata = self._write_metadata_json(job_root, pose_result, pose_summary)
        annotated_video = self._write_annotated_video(job_root, pose_result, video_path)
        annotated_keyframes = [] if annotated_video is not None else self._write_annotated_keyframes(job_root, pose_result, video_path)
        return PoseArtifactBundle(
            landmarks=landmarks,
            metadata=metadata,
            annotated_video=annotated_video,
            annotated_keyframes=annotated_keyframes,
        )

    def _ensure_job_root(self, job_id: str) -> Path:
        job_root = self._storage_root / "pose" / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        return job_root

    def _write_landmarks_json(
        self,
        job_root: Path,
        pose_result: PoseEstimationResult,
        pose_summary: PoseAnalysisSummary,
    ) -> PoseArtifactReference:
        payload = {
            "summary": pose_summary.model_dump(mode="json"),
            "pose": {
                "video_path": pose_result.video_path,
                "model_name": pose_result.model_name,
                "model_path": pose_result.model_path,
                "total_frames": pose_result.total_frames,
                "processed_frames": pose_result.processed_frames,
                "frame_rate": pose_result.frame_rate,
                "width": pose_result.width,
                "height": pose_result.height,
                "duration_seconds": pose_result.duration_seconds,
                "rotation_normalized": pose_result.rotation_normalized,
            },
            "frames": [frame.model_dump(mode="json") for frame in pose_result.frames],
        }
        path = job_root / "landmarks.json"
        self._write_json(path, payload)
        return self._artifact_reference(path, "json", "application/json", filename="landmarks.json")

    def _write_metadata_json(
        self,
        job_root: Path,
        pose_result: PoseEstimationResult,
        pose_summary: PoseAnalysisSummary,
    ) -> PoseArtifactReference:
        payload = {
            "pose": pose_summary.model_dump(mode="json"),
            "video": {
                "model_name": pose_result.model_name,
                "model_path": pose_result.model_path,
                "total_frames": pose_result.total_frames,
                "processed_frames": pose_result.processed_frames,
                "frame_rate": pose_result.frame_rate,
                "rotation_normalized": pose_result.rotation_normalized,
            },
        }
        path = job_root / "pose-metadata.json"
        self._write_json(path, payload)
        return self._artifact_reference(path, "json", "application/json", filename="pose-metadata.json")

    def _write_annotated_video(
        self,
        job_root: Path,
        pose_result: PoseEstimationResult,
        video_path: str,
    ) -> PoseArtifactReference | None:
        output_path = job_root / "annotated.mp4"
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            return None
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)

        fourcc_candidates = ["mp4v", "avc1", "H264"]
        writer = None
        for fourcc_name in fourcc_candidates:
            fourcc = cv2.VideoWriter_fourcc(*fourcc_name)
            writer = cv2.VideoWriter(
                str(output_path),
                fourcc,
                pose_result.frame_rate or 30.0,
                (pose_result.width, pose_result.height),
            )
            if writer.isOpened():
                break
            writer.release()
            writer = None

        if writer is None:
            capture.release()
            return None

        frame_map = {frame.frame_index: frame for frame in pose_result.frames}
        frame_index = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                annotated = self._draw_annotations(frame, frame_map.get(frame_index))
                writer.write(annotated)
                frame_index += 1
        except Exception:
            output_path.unlink(missing_ok=True)
            return None
        finally:
            capture.release()
            writer.release()

        if not output_path.exists() or output_path.stat().st_size <= 0:
            output_path.unlink(missing_ok=True)
            return None

        return self._artifact_reference(output_path, "video", "video/mp4", filename="annotated.mp4")

    def _write_annotated_keyframes(
        self,
        job_root: Path,
        pose_result: PoseEstimationResult,
        video_path: str,
    ) -> list[PoseArtifactReference]:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            return []
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)

        selected_indices = self._sample_indices(pose_result.total_frames or pose_result.processed_frames)
        selected_set = set(selected_indices)
        frame_map = {frame.frame_index: frame for frame in pose_result.frames}
        keyframes: list[PoseArtifactReference] = []

        try:
            frame_index = 0
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                if frame_index in selected_set:
                    annotated = self._draw_annotations(frame, frame_map.get(frame_index))
                    filename = f"annotated-{frame_index:05d}.jpg"
                    path = job_root / filename
                    if cv2.imwrite(str(path), annotated):
                        keyframes.append(self._artifact_reference(path, "image", "image/jpeg", filename=filename, frame_index=frame_index))
                frame_index += 1
        except Exception:
            return keyframes
        finally:
            capture.release()

        return keyframes

    def _draw_annotations(self, frame, frame_record: PoseFrameRecord | None):
        annotated = frame.copy()
        if frame_record is None or not frame_record.landmarks:
            return annotated

        height, width = annotated.shape[:2]
        points: dict[str, tuple[int, int]] = {}
        for landmark in frame_record.landmarks:
            x = self._clamp_int(landmark.x * width, width - 1)
            y = self._clamp_int(landmark.y * height, height - 1)
            points[landmark.landmark_name] = (x, y)
            cv2.circle(annotated, (x, y), 3, (0, 255, 0), -1)

        for start_name, end_name in POSE_CONNECTIONS:
            start = points.get(start_name)
            end = points.get(end_name)
            if start is not None and end is not None:
                cv2.line(annotated, start, end, (255, 180, 0), 2)

        return annotated

    def _sample_indices(self, total_frames: int) -> list[int]:
        if total_frames <= 0:
            return [0]
        indices = {0, max(0, total_frames // 2), max(0, total_frames - 1)}
        return sorted(indices)

    def _artifact_reference(
        self,
        path: Path,
        artifact_type: str,
        mime_type: str,
        *,
        filename: str | None = None,
        frame_index: int | None = None,
    ) -> PoseArtifactReference:
        path.chmod(0o600)
        return PoseArtifactReference(
            type=artifact_type,
            storage_key=str(path.relative_to(self._storage_root)),
            mime_type=mime_type,
            filename=filename or path.name,
            frame_index=frame_index,
            size_bytes=path.stat().st_size,
        )

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        path.chmod(0o600)

    def _clamp_int(self, value: float, max_value: int) -> int:
        return max(0, min(max_value, int(round(value))))
