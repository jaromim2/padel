from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np

from app.pose_models import PoseFrameRecord, PoseLandmarkRecord


def build_synthetic_video(path: Path, *, frame_count: int = 12, size: tuple[int, int] = (64, 64), motion: bool = True) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"XVID"), 10.0, size)
    if not writer.isOpened():
        raise RuntimeError("Unable to create synthetic video.")

    width, height = size
    for frame_index in range(frame_count):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        if motion:
            offset = min(width - 16, 4 + frame_index * 2)
            frame[16:32, offset : min(width, offset + 12)] = (255, 255, 255)
        writer.write(frame)

    writer.release()


def build_pose_frame(
    frame_index: int,
    timestamp_ms: int,
    visibility: float,
    *,
    coordinates: tuple[float, float] = (0.5, 0.5),
) -> PoseFrameRecord:
    x, y = coordinates
    landmarks = [
        PoseLandmarkRecord(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            landmark_name=name,
            x=x,
            y=y,
            z=0.0,
            visibility=visibility,
            presence=visibility,
        )
        for name in (
            "left_shoulder",
            "right_shoulder",
            "left_elbow",
            "right_elbow",
            "left_wrist",
            "right_wrist",
            "left_hip",
            "right_hip",
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
        )
    ]
    return PoseFrameRecord(frame_index=frame_index, timestamp_ms=timestamp_ms, landmarks=landmarks)


def build_forehand_pose_sequence(
    *,
    handedness: str = "right",
    frame_count: int = 12,
    visibility: float = 0.95,
    visibility_overrides: dict[str, float] | None = None,
    occluded_landmarks: Iterable[str] | None = None,
) -> list[PoseFrameRecord]:
    occluded = set(occluded_landmarks or [])
    frames: list[PoseFrameRecord] = []
    for frame_index in range(frame_count):
        progress = 0.0 if frame_count <= 1 else frame_index / float(frame_count - 1)
        swing = _swing_progress(progress)
        landmarks = _build_right_handed_forehand_landmarks(
            frame_index,
            int(round(frame_index * 100)),
            swing,
            visibility=visibility,
            visibility_overrides=visibility_overrides,
            occluded_landmarks=occluded,
        )
        if handedness.strip().lower() == "left":
            landmarks = _mirror_landmarks(landmarks)
        frames.append(PoseFrameRecord(frame_index=frame_index, timestamp_ms=int(round(frame_index * 100)), landmarks=landmarks))
    return frames


def _swing_progress(progress: float) -> float:
    if progress <= 0.25:
        return progress * 0.25
    if progress <= 0.6:
        return 0.0625 + ((progress - 0.25) / 0.35) * 0.42
    if progress <= 0.85:
        return 0.4825 + ((progress - 0.6) / 0.25) * 0.35
    return 0.8325 + ((progress - 0.85) / 0.15) * 0.1675


def _build_right_handed_forehand_landmarks(
    frame_index: int,
    timestamp_ms: int,
    swing: float,
    *,
    visibility: float,
    visibility_overrides: dict[str, float] | None,
    occluded_landmarks: set[str],
) -> list[PoseLandmarkRecord]:
    coords: dict[str, tuple[float, float]] = {
        "left_shoulder": (0.40, 0.36),
        "right_shoulder": (0.60, 0.36),
        "left_elbow": (0.35, 0.49),
        "right_elbow": (0.66 - (0.03 * swing), 0.47 - (0.05 * swing)),
        "left_wrist": (0.31, 0.58),
        "right_wrist": (0.70 + (0.12 * swing), 0.58 - (0.16 * swing)),
        "left_hip": (0.44, 0.62),
        "right_hip": (0.56, 0.62),
        "left_knee": (0.45, 0.80),
        "right_knee": (0.55, 0.80),
        "left_ankle": (0.46, 0.97),
        "right_ankle": (0.54, 0.97),
        "left_heel": (0.46, 0.99),
        "right_heel": (0.54, 0.99),
        "left_foot_index": (0.47, 1.0),
        "right_foot_index": (0.53, 1.0),
    }
    if swing > 0.4:
        coords["right_elbow"] = (0.64 + (0.02 * swing), 0.44 - (0.02 * swing))
    if swing > 0.6:
        coords["right_wrist"] = (0.78 + (0.05 * (swing - 0.6)), 0.47 - (0.05 * (swing - 0.6)))

    landmarks: list[PoseLandmarkRecord] = []
    for name, (x, y) in coords.items():
        if name in occluded_landmarks:
            current_visibility = 0.05
        else:
            current_visibility = visibility_overrides.get(name, visibility) if visibility_overrides else visibility
        landmarks.append(
            PoseLandmarkRecord(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                landmark_name=name,
                x=x,
                y=y,
                z=0.0,
                visibility=current_visibility,
                presence=current_visibility,
            )
        )
    return landmarks


def _mirror_landmarks(landmarks: list[PoseLandmarkRecord]) -> list[PoseLandmarkRecord]:
    mirrored: list[PoseLandmarkRecord] = []
    for landmark in landmarks:
        if landmark.landmark_name.startswith("left_"):
            name = "right_" + landmark.landmark_name.removeprefix("left_")
        elif landmark.landmark_name.startswith("right_"):
            name = "left_" + landmark.landmark_name.removeprefix("right_")
        else:
            name = landmark.landmark_name
        mirrored.append(
            PoseLandmarkRecord(
                frame_index=landmark.frame_index,
                timestamp_ms=landmark.timestamp_ms,
                landmark_name=name,
                x=1.0 - landmark.x,
                y=landmark.y,
                z=landmark.z,
                visibility=landmark.visibility,
                presence=landmark.presence,
            )
        )
    return mirrored
