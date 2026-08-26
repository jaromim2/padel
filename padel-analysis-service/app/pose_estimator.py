from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from .pose_models import POSE_LANDMARK_NAMES, PoseEstimationResult, PoseFrameRecord, PoseLandmarkRecord


@dataclass(frozen=True, slots=True)
class PoseProcessingError(RuntimeError):
    code: str
    message: str
    detail: dict[str, object] | list[object] | str | None = None

    def __str__(self) -> str:
        return self.message


class PoseEstimator(Protocol):
    def estimate(self, video_path: str) -> PoseEstimationResult:
        ...


class MediaPipePoseEstimator:
    def __init__(
        self,
        *,
        model_path: str,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        max_num_poses: int = 1,
    ) -> None:
        self._model_path = model_path
        self._min_pose_detection_confidence = min_pose_detection_confidence
        self._min_pose_presence_confidence = min_pose_presence_confidence
        self._min_tracking_confidence = min_tracking_confidence
        self._max_num_poses = max_num_poses

    def estimate(self, video_path: str) -> PoseEstimationResult:
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
        except Exception as exc:  # noqa: BLE001
            raise PoseProcessingError(
                code="pose_mediapipe_unavailable",
                message="MediaPipe is not available.",
                detail={"type": exc.__class__.__name__},
            ) from exc

        if self._model_path.strip() == "":
            raise PoseProcessingError(
                code="pose_model_missing",
                message="Pose model path is not configured.",
            )

        landmarker = None
        try:
            base_options = python.BaseOptions(model_asset_path=self._model_path)
            options = vision.PoseLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,
                num_poses=self._max_num_poses,
                min_pose_detection_confidence=self._min_pose_detection_confidence,
                min_pose_presence_confidence=self._min_pose_presence_confidence,
                min_tracking_confidence=self._min_tracking_confidence,
            )
            landmarker = vision.PoseLandmarker.create_from_options(options)
        except Exception as exc:  # noqa: BLE001
            raise PoseProcessingError(
                code="pose_landmarker_init_failed",
                message="Unable to initialize the pose landmarker.",
                detail={"type": exc.__class__.__name__},
            ) from exc

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise PoseProcessingError(
                code="pose_video_open_failed",
                message="Unable to open video file.",
            )

        rotation_normalized = False
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            rotation_normalized = bool(capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1))

        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frame_rate = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if total_frames <= 0:
            total_frames = 0
        if width <= 0 or height <= 0:
            capture.release()
            raise PoseProcessingError(
                code="pose_video_probe_failed",
                message="Unable to determine video dimensions.",
            )

        frames: list[PoseFrameRecord] = []
        processed_frames = 0
        try:
            frame_index = 0
            while True:
                ok, frame_bgr = capture.read()
                if not ok or frame_bgr is None:
                    break

                timestamp_ms = self._timestamp_ms(frame_index, frame_rate)
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame_rgb))
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                frame_landmarks: list[PoseLandmarkRecord] = []
                pose_landmarks = result.pose_landmarks[0] if result.pose_landmarks else []
                for landmark_index, landmark in enumerate(pose_landmarks):
                    frame_landmarks.append(
                        PoseLandmarkRecord(
                            frame_index=frame_index,
                            timestamp_ms=timestamp_ms,
                            landmark_name=POSE_LANDMARK_NAMES[landmark_index],
                            x=float(landmark.x),
                            y=float(landmark.y),
                            z=float(landmark.z),
                            visibility=float(landmark.visibility) if getattr(landmark, "visibility", None) is not None else None,
                            presence=float(getattr(landmark, "presence", None)) if getattr(landmark, "presence", None) is not None else None,
                        )
                    )

                frames.append(
                    PoseFrameRecord(
                        frame_index=frame_index,
                        timestamp_ms=timestamp_ms,
                        landmarks=frame_landmarks,
                    )
                )
                processed_frames += 1
                frame_index += 1
        except PoseProcessingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PoseProcessingError(
                code="pose_estimation_failed",
                message="Pose estimation failed.",
                detail={"type": exc.__class__.__name__},
            ) from exc
        finally:
            capture.release()
            if landmarker is not None:
                landmarker.close()

        duration_seconds = float(total_frames / frame_rate) if total_frames > 0 and frame_rate > 0 else 0.0
        return PoseEstimationResult(
            video_path=video_path,
            model_name="pose_landmarker_lite",
            model_path=self._model_path,
            total_frames=total_frames or processed_frames,
            processed_frames=processed_frames,
            frame_rate=frame_rate,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            rotation_normalized=rotation_normalized,
            frames=frames,
        )

    def _timestamp_ms(self, frame_index: int, frame_rate: float) -> int:
        if frame_rate <= 0:
            return frame_index * 33
        return int(round((frame_index / frame_rate) * 1000.0))
