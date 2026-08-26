from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from .match_models import MatchBoundingBox, MatchPlayerCandidate


@dataclass(slots=True)
class TrackingObservation:
    tracked: bool
    box: MatchBoundingBox | None
    confidence: float = 0.0


class PlayerDetector(Protocol):
    def detect(self, frame: np.ndarray) -> list[MatchPlayerCandidate]:
        ...


class MultiObjectTracker(Protocol):
    def initialize(self, frame: np.ndarray, box: MatchBoundingBox) -> None:
        ...

    def update(self, frame: np.ndarray) -> TrackingObservation:
        ...


class SelectedPlayerResolver(Protocol):
    def resolve(
        self,
        candidates: Sequence[MatchPlayerCandidate],
        selection: object | None = None,
    ) -> MatchPlayerCandidate | None:
        ...


class OpenCVPlayerDetector:
    def __init__(self) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray) -> list[MatchPlayerCandidate]:
        if frame is None or frame.size == 0:
            return []

        height, width = frame.shape[:2]
        scale = 1.0
        max_dim = max(width, height)
        if max_dim > 960:
            scale = 960.0 / float(max_dim)

        resized = frame
        if scale != 1.0:
            resized = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)

        rects, weights = self._hog.detectMultiScale(
            resized,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
        )
        candidates: list[MatchPlayerCandidate] = []
        for index, ((x, y, w, h), weight) in enumerate(zip(rects, weights)):
            confidence = float(max(0.0, min(1.0, weight)))
            if scale != 1.0:
                inv = 1.0 / scale
                x = int(round(x * inv))
                y = int(round(y * inv))
                w = int(round(w * inv))
                h = int(round(h * inv))
            box = MatchBoundingBox(x=max(0, x), y=max(0, y), width=max(1, w), height=max(1, h))
            candidates.append(
                MatchPlayerCandidate(
                    candidate_id=f"player-{index + 1}",
                    label=f"Player {index + 1}",
                    box=box,
                    confidence=confidence,
                    evidence=["hog_people_detector"],
                )
            )

        return self._non_max_suppression(candidates)

    def _non_max_suppression(self, candidates: list[MatchPlayerCandidate]) -> list[MatchPlayerCandidate]:
        if len(candidates) <= 1:
            return candidates

        ordered = sorted(candidates, key=lambda candidate: (candidate.confidence, candidate.box.width * candidate.box.height), reverse=True)
        selected: list[MatchPlayerCandidate] = []
        for candidate in ordered:
            if any(self._iou(candidate.box, other.box) > 0.35 for other in selected):
                continue
            selected.append(candidate)

        return sorted(selected, key=lambda candidate: (candidate.box.x, candidate.box.y))

    def _iou(self, a: MatchBoundingBox, b: MatchBoundingBox) -> float:
        left = max(a.x, b.x)
        top = max(a.y, b.y)
        right = min(a.x + a.width, b.x + b.width)
        bottom = min(a.y + a.height, b.y + b.height)
        if right <= left or bottom <= top:
            return 0.0
        intersection = float((right - left) * (bottom - top))
        union = float(a.width * a.height + b.width * b.height - intersection)
        return intersection / union if union > 0 else 0.0


class OpenCVMultiObjectTracker:
    def __init__(self) -> None:
        self._tracker = self._build_tracker()
        self._initialized = False

    def initialize(self, frame: np.ndarray, box: MatchBoundingBox) -> None:
        self._tracker = self._build_tracker()
        self._initialized = bool(
            self._tracker.init(
                frame,
                (int(box.x), int(box.y), int(box.width), int(box.height)),
            )
        )

    def update(self, frame: np.ndarray) -> TrackingObservation:
        if not self._initialized:
            return TrackingObservation(tracked=False, box=None, confidence=0.0)

        ok, rect = self._tracker.update(frame)
        if not ok:
            return TrackingObservation(tracked=False, box=None, confidence=0.0)

        x, y, w, h = (int(round(value)) for value in rect)
        confidence = min(1.0, max(0.25, float(w * h) / max(1.0, frame.shape[0] * frame.shape[1])))
        return TrackingObservation(
            tracked=True,
            box=MatchBoundingBox(x=max(0, x), y=max(0, y), width=max(1, w), height=max(1, h)),
            confidence=confidence,
        )

    def _build_tracker(self):
        if hasattr(cv2, "TrackerCSRT_create"):
            return cv2.TrackerCSRT_create()
        if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerCSRT_create"):
            return cv2.legacy.TrackerCSRT_create()
        if hasattr(cv2, "TrackerKCF_create"):
            return cv2.TrackerKCF_create()
        if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerKCF_create"):
            return cv2.legacy.TrackerKCF_create()
        if hasattr(cv2, "TrackerMIL_create"):
            return cv2.TrackerMIL_create()
        if hasattr(cv2, "legacy") and hasattr(cv2.legacy, "TrackerMIL_create"):
            return cv2.legacy.TrackerMIL_create()
        raise RuntimeError("No supported OpenCV tracker available")


class DefaultSelectedPlayerResolver:
    def resolve(
        self,
        candidates: Sequence[MatchPlayerCandidate],
        selection: object | None = None,
    ) -> MatchPlayerCandidate | None:
        if not candidates:
            return None

        if isinstance(selection, str) and selection:
            for candidate in candidates:
                if candidate.candidate_id == selection:
                    return candidate

        if isinstance(selection, int) and 0 <= selection < len(candidates):
            return candidates[selection]

        if isinstance(selection, dict):
            candidate_id = str(selection.get("candidate_id", "")).strip()
            if candidate_id:
                for candidate in candidates:
                    if candidate.candidate_id == candidate_id:
                        return candidate

        ordered = sorted(
            candidates,
            key=lambda candidate: (candidate.confidence, candidate.box.width * candidate.box.height),
            reverse=True,
        )
        return ordered[0] if ordered else None
