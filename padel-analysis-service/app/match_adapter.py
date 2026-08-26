from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
import math
import os
import subprocess
from pathlib import Path
from statistics import median

import cv2
import numpy as np

from .match_components import (
    DefaultSelectedPlayerResolver,
    MultiObjectTracker,
    OpenCVPlayerDetector,
    OpenCVMultiObjectTracker,
    PlayerDetector,
    SelectedPlayerResolver,
    TrackingObservation,
)
from .match_models import (
    MatchAnalysisSummary,
    MatchArtifactBundle,
    MatchBoundingBox,
    MatchPlayerCandidate,
    MatchPreviewFrame,
    MatchPreviewSummary,
    MatchStrokeCandidate,
    MatchStrokeCandidateArtifacts,
    MatchTrackFrame,
    MatchTrackingInterval,
    MatchTrackingSummary,
)
from .models import JobResult
from .pose_models import PoseArtifactReference, PoseVideoSummary


@dataclass(frozen=True, slots=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float


class MatchAnalysisError(RuntimeError):
    def __init__(self, code: str, message: str, detail: dict[str, object] | list[object] | str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class MatchAnalysisAdapter:
    def __init__(
        self,
        *,
        storage_root: Path | None = None,
        detector: PlayerDetector | None = None,
        tracker_factory: Callable[[], MultiObjectTracker] | None = None,
        resolver: SelectedPlayerResolver | None = None,
    ) -> None:
        self._storage_root = storage_root or Path(os.getenv("PADEL_ANALYSIS_SERVICE_STORAGE_DIR", "/tmp/padel-analysis-service/storage"))
        self._detector = detector or OpenCVPlayerDetector()
        self._tracker_factory = tracker_factory or OpenCVMultiObjectTracker
        self._resolver = resolver or DefaultSelectedPlayerResolver()

    def analyze(self, job, video_path: str) -> JobResult:  # type: ignore[override]
        probe = self._probe(video_path)
        preview_frames = self._sample_preview_frames(job.job_id, video_path, probe)
        if not preview_frames:
            raise MatchAnalysisError("match_preview_frame_missing", "Unable to read a preview frame from the uploaded match video.")

        selected_preview = self._select_best_preview_frame(preview_frames)
        preview_summary = MatchPreviewSummary(
            frame_index=selected_preview.frame_index,
            timestamp_ms=selected_preview.timestamp_ms,
            width=probe.width,
            height=probe.height,
            candidates=selected_preview.candidates,
            reasons=selected_preview.reasons,
            frames=preview_frames,
        )
        video_summary = self._build_video_summary(probe)

        selected_candidate_id = self._selected_candidate_id(job)
        if not selected_candidate_id:
            return self._preview_result(job, video_summary, preview_summary, selected_preview.artifact)

        selected_candidate = self._resolve_preview_candidate(preview_frames, selected_candidate_id)
        if selected_candidate is None:
            raise MatchAnalysisError(
                "match_selected_player_not_found",
                "The selected player could not be resolved from the detected preview boxes.",
            )

        return self._completed_result(
            job=job,
            video_path=video_path,
            probe=probe,
            video_summary=video_summary,
            preview_summary=preview_summary,
            preview_artifact=selected_preview.artifact,
            selected_candidate=selected_candidate,
        )

    def _preview_result(
        self,
        job,
        video_summary: PoseVideoSummary,
        preview_summary: MatchPreviewSummary,
        preview_artifact: PoseArtifactReference | None,
    ) -> JobResult:
        has_any_candidates = any(frame.candidates for frame in preview_summary.frames) or bool(preview_summary.candidates)
        if not has_any_candidates:
            reasons = self._dedupe([
                "no plausible player candidates were detected across sampled preview frames",
                *preview_summary.reasons,
            ])
            match_summary = MatchAnalysisSummary(
                enabled=True,
                available=False,
                stage="unsupported",
                selection_required=False,
                preview=preview_summary,
                artifacts=MatchArtifactBundle(preview_image=preview_artifact),
                reasons=reasons,
            )
            return JobResult(
                job_status="completed",
                summary="Match video is unsupported for the MVP preview flow.",
                findings=[
                    f"Preview frame analyzed at {preview_summary.timestamp_ms} ms.",
                    "No plausible player candidate was found in the sampled frames.",
                ],
                recommendations=[
                    "Use a fixed camera angle that keeps the whole court visible.",
                    "Make sure at least one player is visible in the sampled preview section.",
                ],
                analysis_id=job.analysis_id,
                job_id=job.job_id,
                processor="opencv_match_tracker",
                service_version=job.result.service_version if job.result else "0.1.0",
                video=video_summary,
                match=match_summary,
            )

        reasons = list(preview_summary.reasons)
        if len(preview_summary.candidates) < 4:
            reasons.append("less than four players were visible in the selected preview frame")
        reasons.append("use the preview controls to move between candidate frames before confirming a player")
        reasons = self._dedupe(reasons)

        match_summary = MatchAnalysisSummary(
            enabled=True,
            available=True,
            stage="selection_required",
            selection_required=True,
            preview=preview_summary,
            artifacts=MatchArtifactBundle(preview_image=preview_artifact),
            reasons=reasons,
        )
        return JobResult(
            job_status="awaiting_player_selection",
            summary="Match preview is ready. Select one player to continue.",
            findings=[
                f"Detected {len(preview_summary.candidates)} likely players on the selected preview frame.",
                "Choose the player you want to track for stroke-window extraction.",
            ],
            recommendations=[
                "Select the most relevant player using the preview boxes.",
                "Use a fixed side view for the most reliable tracking.",
            ],
            analysis_id=job.analysis_id,
            job_id=job.job_id,
            processor="opencv_match_tracker",
            service_version=job.result.service_version if job.result else "0.1.0",
            video=video_summary,
            match=match_summary,
        )

    def _completed_result(
        self,
        *,
        job,
        video_path: str,
        probe: VideoProbe,
        video_summary: PoseVideoSummary,
        preview_summary: MatchPreviewSummary,
        preview_artifact: PoseArtifactReference | None,
        selected_candidate: MatchPlayerCandidate,
    ) -> JobResult:
        tracking = self._track_selected_player(video_path, probe, selected_candidate)
        tracking_preview_frame_index = self._candidate_frame_index(selected_candidate.candidate_id) or preview_summary.frame_index
        tracking_artifact = self._write_tracking_preview_artifact(job.job_id, video_path, tracking_preview_frame_index, selected_candidate)
        stroke_candidates = self._detect_stroke_candidates(job.job_id, video_path, probe, tracking)
        metadata_artifact = self._write_metadata_artifact(
            job.job_id,
            probe,
            preview_summary,
            tracking,
            selected_candidate,
            stroke_candidates,
        )
        match_summary = MatchAnalysisSummary(
            enabled=True,
            available=True,
            stage="completed",
            selection_required=False,
            selected_player_candidate_id=selected_candidate.candidate_id,
            selected_player_label=selected_candidate.label,
            preview=preview_summary,
            tracking=tracking,
            stroke_candidates=stroke_candidates,
            artifacts=MatchArtifactBundle(
                preview_image=preview_artifact,
                tracking_preview_image=tracking_artifact,
                metadata=metadata_artifact,
            ),
            reasons=tracking.reasons,
        )

        findings = [
            f"Selected player: {selected_candidate.label}",
            f"Tracking coverage: {tracking.coverage:.1%}",
            f"Detected stroke candidates: {len(stroke_candidates)}",
        ]
        if tracking.reasons:
            findings.extend(f"Tracking note: {reason}" for reason in tracking.reasons)

        return JobResult(
            job_status="completed",
            summary="Match stroke-window extraction completed successfully.",
            findings=findings,
            recommendations=[
                "Review the private stroke clips and mark each candidate as real, false, missed nearby, or unclear.",
            ],
            analysis_id=job.analysis_id,
            job_id=job.job_id,
            processor="opencv_match_tracker",
            service_version=job.result.service_version if job.result else "0.1.0",
            video=video_summary,
            match=match_summary,
        )

    def _track_selected_player(
        self,
        video_path: str,
        probe: VideoProbe,
        selected_candidate: MatchPlayerCandidate,
    ) -> MatchTrackingSummary:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise MatchAnalysisError("match_video_open_failed", "Unable to open match video.")

        tracker = self._tracker_factory()
        ok, first_frame = capture.read()
        if not ok or first_frame is None:
            capture.release()
            raise MatchAnalysisError("match_video_read_failed", "Unable to read the first frame.")

        tracker.initialize(first_frame, selected_candidate.box)
        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)

        frames: list[MatchTrackFrame] = []
        tracked_frames = 0
        missing_intervals: list[MatchTrackingInterval] = []
        interval_start: int | None = None
        track_confidences: list[float] = []

        frame_index = 0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break

            if frame_index == 0:
                observation = TrackingObservation(tracked=True, box=selected_candidate.box, confidence=selected_candidate.confidence)
            else:
                observation = tracker.update(frame)
                if not observation.tracked:
                    observation = self._attempt_reacquire(frame, frames[-1] if frames else None, tracker)

            tracked = observation.tracked and observation.box is not None
            if tracked:
                tracked_frames += 1
                track_confidences.append(float(observation.confidence))
                if interval_start is not None:
                    missing_intervals.append(MatchTrackingInterval(start_frame=interval_start, end_frame=max(0, frame_index - 1), reason="temporary_tracking_loss"))
                    interval_start = None
            else:
                if interval_start is None:
                    interval_start = frame_index

            frames.append(
                MatchTrackFrame(
                    frame_index=frame_index,
                    timestamp_ms=self._timestamp_ms(frame_index, probe.fps),
                    tracked=tracked,
                    confidence=float(observation.confidence if tracked else 0.0),
                    box=observation.box if tracked else None,
                )
            )
            frame_index += 1

        capture.release()
        if interval_start is not None:
            missing_intervals.append(MatchTrackingInterval(start_frame=interval_start, end_frame=max(0, frame_index - 1), reason="tracking_loss"))

        coverage = tracked_frames / float(max(1, probe.frame_count))
        average_confidence = self._mean(track_confidences)
        confidence_level = self._classify_tracking_confidence(coverage, average_confidence, missing_intervals)
        reasons = self._tracking_reasons(coverage, average_confidence, missing_intervals)

        return MatchTrackingSummary(
            track_id=f"{selected_candidate.candidate_id}-track",
            selected_player_candidate_id=selected_candidate.candidate_id,
            selected_player_label=selected_candidate.label,
            coverage=coverage,
            confidence_level=confidence_level,
            total_frames=probe.frame_count,
            tracked_frames=tracked_frames,
            missing_intervals=missing_intervals,
            frames=frames,
            reasons=reasons,
        )

    def _attempt_reacquire(
        self,
        frame: np.ndarray,
        last_frame: MatchTrackFrame | None,
        tracker: MultiObjectTracker,
    ) -> TrackingObservation:
        candidates = self._detector.detect(frame)
        if not candidates:
            return TrackingObservation(tracked=False, box=None, confidence=0.0)

        if last_frame is not None and last_frame.box is not None:
            candidate = max(candidates, key=lambda item: self._iou(item.box, last_frame.box))
            if self._iou(candidate.box, last_frame.box) < 0.1:
                return TrackingObservation(tracked=False, box=None, confidence=0.0)
        else:
            candidate = candidates[0]

        tracker.initialize(frame, candidate.box)
        return TrackingObservation(tracked=True, box=candidate.box, confidence=max(0.5, candidate.confidence))

    def _detect_stroke_candidates(
        self,
        job_id: str,
        video_path: str,
        probe: VideoProbe,
        tracking: MatchTrackingSummary,
    ) -> list[MatchStrokeCandidate]:
        motion_signal: list[float] = []
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise MatchAnalysisError("match_video_open_failed", "Unable to reopen the match video for stroke detection.")

        previous_crop: np.ndarray | None = None
        for frame in tracking.frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame.frame_index))
            ok, image = capture.read()
            if not ok or image is None or frame.box is None:
                motion_signal.append(0.0)
                continue

            crop = self._crop_frame(image, frame.box)
            if crop is None:
                motion_signal.append(0.0)
                continue

            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
            current = gray.astype(np.float32)
            if previous_crop is None:
                motion_signal.append(0.0)
            else:
                motion_signal.append(float(np.mean(np.abs(current - previous_crop)) / 255.0))
            previous_crop = current

        capture.release()
        if not motion_signal or not any(motion_signal):
            return []

        smoothed = self._smooth_signal(motion_signal, window=5)
        baseline = median(smoothed[: max(5, min(len(smoothed), max(8, int(round(probe.fps * 0.8)))))])
        stdev = float(np.std(np.asarray(smoothed, dtype=np.float32)))
        threshold = baseline + max(0.01, stdev * 1.25)
        min_gap = max(6, int(round(probe.fps * 0.6)))
        pre_window = max(1, int(round(probe.fps * 1.5)))
        post_window = max(1, int(round(probe.fps * 1.5)))

        peak_frames = self._find_peaks(smoothed, threshold=threshold, min_gap=min_gap)
        if not peak_frames and max(smoothed) > baseline + 0.01:
            peak_frames = [int(np.argmax(smoothed))]

        candidates: list[MatchStrokeCandidate] = []
        for index, peak_frame in enumerate(peak_frames, start=1):
            peak_value = smoothed[peak_frame]
            if peak_value < threshold and len(peak_frames) > 1:
                continue

            start_frame = max(0, peak_frame - pre_window)
            end_frame = min(probe.frame_count - 1, peak_frame + post_window)
            local_track_confidence = self._mean(
                frame.confidence
                for frame in tracking.frames[start_frame : end_frame + 1]
                if frame.tracked
            )
            candidates.append(
                MatchStrokeCandidate(
                    candidate_id=f"candidate-{index}",
                    start_frame=start_frame,
                    start_timestamp_ms=self._timestamp_ms(start_frame, probe.fps),
                    peak_frame=peak_frame,
                    peak_timestamp_ms=self._timestamp_ms(peak_frame, probe.fps),
                    end_frame=end_frame,
                    end_timestamp_ms=self._timestamp_ms(end_frame, probe.fps),
                    confidence=self._classify_candidate_confidence(peak_value, baseline, stdev, local_track_confidence),
                    evidence=[
                        f"motion_peak={peak_value:.4f}",
                        f"baseline={baseline:.4f}",
                        f"track_confidence={local_track_confidence:.4f}",
                    ],
                    selected_player_track_confidence=local_track_confidence,
                )
            )

        candidates = self._dedupe_candidates(candidates)
        self._extract_candidate_artifacts(job_id, video_path, probe, tracking, candidates)
        return candidates

    def _extract_candidate_artifacts(
        self,
        job_id: str,
        video_path: str,
        probe: VideoProbe,
        tracking: MatchTrackingSummary,
        candidates: list[MatchStrokeCandidate],
    ) -> None:
        root = self._artifact_root(job_id)
        root.mkdir(parents=True, exist_ok=True)
        for candidate in candidates:
            box = self._average_box(tracking.frames[candidate.start_frame : candidate.end_frame + 1])
            clip_path = root / f"{candidate.candidate_id}-clip.mp4"
            thumbnail_path = root / f"{candidate.candidate_id}-thumbnail.jpg"

            try:
                self._write_candidate_clip(video_path, clip_path, candidate, probe, box)
            except MatchAnalysisError as exc:
                candidate.rejection_reasons.append(exc.code)
                clip_path = None

            try:
                self._write_candidate_thumbnail(video_path, thumbnail_path, candidate, box)
            except MatchAnalysisError as exc:
                candidate.rejection_reasons.append(exc.code)
                thumbnail_path = None

            candidate.artifacts = MatchStrokeCandidateArtifacts(
                clip=self._artifact_reference(clip_path, "video/mp4", "clip") if clip_path and clip_path.exists() else None,
                thumbnail=self._artifact_reference(thumbnail_path, "image/jpeg", "thumbnail") if thumbnail_path and thumbnail_path.exists() else None,
            )

    def _write_preview_artifact(
        self,
        job_id: str,
        frame: np.ndarray,
        candidates: list[MatchPlayerCandidate],
        *,
        artifact_name: str = "match-preview.jpg",
    ) -> PoseArtifactReference | None:
        root = self._artifact_root(job_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / artifact_name
        annotated = frame.copy()
        for candidate in candidates:
            x, y, w, h = candidate.box.x, candidate.box.y, candidate.box.width, candidate.box.height
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (74, 220, 180), 2)
            cv2.putText(
                annotated,
                candidate.label,
                (x, max(20, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        if not cv2.imwrite(str(path), annotated):
            return None
        return self._artifact_reference(path, "image/jpeg", "preview")

    def _write_tracking_preview_artifact(
        self,
        job_id: str,
        video_path: str,
        frame_index: int,
        selected_candidate: MatchPlayerCandidate,
    ) -> PoseArtifactReference | None:
        frame = self._read_frame_at(video_path, frame_index)
        if frame is None:
            return None

        root = self._artifact_root(job_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "tracking-preview.jpg"
        annotated = frame.copy()
        cv2.rectangle(
            annotated,
            (selected_candidate.box.x, selected_candidate.box.y),
            (selected_candidate.box.x + selected_candidate.box.width, selected_candidate.box.y + selected_candidate.box.height),
            (74, 220, 180),
            3,
        )
        cv2.putText(
            annotated,
            f"{selected_candidate.label} / selected",
            (max(10, selected_candidate.box.x), max(20, selected_candidate.box.y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        if not cv2.imwrite(str(path), annotated):
            return None
        return self._artifact_reference(path, "image/jpeg", "tracking_preview")

    def _write_metadata_artifact(
        self,
        job_id: str,
        probe: VideoProbe,
        preview_summary: MatchPreviewSummary,
        tracking: MatchTrackingSummary,
        selected_candidate: MatchPlayerCandidate,
        candidates: list[MatchStrokeCandidate],
    ) -> PoseArtifactReference | None:
        root = self._artifact_root(job_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "match-metadata.json"
        payload = {
            "probe": {
                "width": probe.width,
                "height": probe.height,
                "fps": probe.fps,
                "frame_count": probe.frame_count,
                "duration_seconds": probe.duration_seconds,
            },
            "preview": preview_summary.model_dump(mode="json"),
            "selected_player": selected_candidate.model_dump(mode="json"),
            "tracking": tracking.model_dump(mode="json"),
            "stroke_candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return self._artifact_reference(path, "application/json", "metadata")

    def _write_candidate_clip(
        self,
        video_path: str,
        clip_path: Path,
        candidate: MatchStrokeCandidate,
        probe: VideoProbe,
        box: MatchBoundingBox | None,
    ) -> None:
        if box is None:
            raise MatchAnalysisError("match_candidate_clip_missing_box", "Cannot extract a clip without a tracking box.")

        ffmpeg = self._ffmpeg_path()
        if ffmpeg == "":
            raise MatchAnalysisError("match_ffmpeg_missing", "ffmpeg is not available for clip extraction.")

        duration_seconds = max(1.0, (candidate.end_timestamp_ms - candidate.start_timestamp_ms) / 1000.0)
        start_seconds = max(0.0, candidate.start_timestamp_ms / 1000.0)
        crop = self._crop_filter(box, probe.width, probe.height, padding=0.28)
        command = [
            ffmpeg,
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            video_path,
            "-t",
            f"{duration_seconds:.3f}",
            "-vf",
            crop,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(clip_path),
        ]
        self._run(command, "match_candidate_clip_failed")

    def _write_candidate_thumbnail(
        self,
        video_path: str,
        thumbnail_path: Path,
        candidate: MatchStrokeCandidate,
        box: MatchBoundingBox | None,
    ) -> None:
        frame = self._read_frame_at(video_path, candidate.peak_frame)
        if frame is None:
            raise MatchAnalysisError("match_thumbnail_read_failed", "Unable to read the peak frame.")
        annotated = frame.copy()
        if box is not None:
            cv2.rectangle(annotated, (box.x, box.y), (box.x + box.width, box.y + box.height), (74, 220, 180), 2)
        cv2.putText(
            annotated,
            candidate.candidate_id,
            (max(10, box.x if box else 10), max(20, (box.y if box else 20) - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        if not cv2.imwrite(str(thumbnail_path), annotated):
            raise MatchAnalysisError("match_thumbnail_write_failed", "Unable to write the candidate thumbnail.")

    def _build_video_summary(self, probe: VideoProbe) -> PoseVideoSummary:
        return PoseVideoSummary(
            total_frames=probe.frame_count,
            processed_frames=probe.frame_count,
            frame_rate=probe.fps,
            width=probe.width,
            height=probe.height,
            duration_seconds=probe.duration_seconds,
            motion_score=0.0,
            motion_label="stable",
            rotation_normalized=False,
        )

    def _sample_preview_frames(self, job_id: str, video_path: str, probe: VideoProbe) -> list[MatchPreviewFrame]:
        sample_indexes = self._sample_preview_frame_indexes(probe)
        sampled: list[tuple[np.ndarray, MatchPreviewFrame]] = []
        previous_frame: np.ndarray | None = None

        for frame_index in sample_indexes:
            frame = self._read_frame_at(video_path, frame_index)
            if frame is None:
                continue

            candidates = self._detect_candidates(frame, frame_index)
            score, reasons = self._score_preview_frame(frame, candidates, previous_frame)
            preview_frame = MatchPreviewFrame(
                frame_index=frame_index,
                timestamp_ms=self._timestamp_ms(frame_index, probe.fps),
                width=probe.width,
                height=probe.height,
                candidates=candidates,
                reasons=reasons,
                quality_score=score,
            )
            sampled.append((frame, preview_frame))
            previous_frame = frame

        if not sampled:
            return []

        best_frame = max(
            (item[1] for item in sampled),
            key=lambda preview: (
                len(preview.candidates),
                preview.quality_score,
                -preview.frame_index,
            ),
        )

        frames: list[MatchPreviewFrame] = []
        for frame, preview_frame in sampled:
            artifact_name = "match-preview.jpg" if preview_frame.frame_index == best_frame.frame_index else f"preview-frame-{preview_frame.frame_index}.jpg"
            preview_frame.artifact = self._write_preview_artifact(job_id, frame, preview_frame.candidates, artifact_name=artifact_name)
            frames.append(preview_frame)

        return sorted(frames, key=lambda item: item.frame_index)

    def _select_best_preview_frame(self, frames: list[MatchPreviewFrame]) -> MatchPreviewFrame:
        if not frames:
            raise MatchAnalysisError("match_preview_frame_missing", "Unable to read a preview frame from the uploaded match video.")
        return max(
            frames,
            key=lambda preview: (
                len(preview.candidates),
                preview.quality_score,
                -preview.frame_index,
            ),
        )

    def _resolve_preview_candidate(self, frames: list[MatchPreviewFrame], candidate_id: str) -> MatchPlayerCandidate | None:
        for frame in frames:
            for candidate in frame.candidates:
                if candidate.candidate_id == candidate_id:
                    return candidate
        return None

    def _candidate_frame_index(self, candidate_id: str) -> int | None:
        parts = candidate_id.split("-")
        if len(parts) < 4 or parts[0] != "frame":
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    def _sample_preview_frame_indexes(self, probe: VideoProbe) -> list[int]:
        sample_count = int(os.getenv("PADEL_MATCH_PREVIEW_SAMPLE_COUNT", "6"))
        sample_count = max(3, min(8, sample_count))
        window_seconds = float(os.getenv("PADEL_MATCH_PREVIEW_WINDOW_SECONDS", "12"))
        end_frame = min(probe.frame_count - 1, max(1, int(round(min(window_seconds, max(2.0, probe.duration_seconds * 0.35)) * probe.fps))))
        start_frame = max(0, min(end_frame, int(round(probe.fps * 0.5))))
        if end_frame <= start_frame:
            return [max(0, min(probe.frame_count - 1, start_frame))]
        return sorted({int(round(value)) for value in np.linspace(start_frame, end_frame, num=sample_count, dtype=float)})

    def _score_preview_frame(
        self,
        frame: np.ndarray,
        candidates: list[MatchPlayerCandidate],
        previous_frame: np.ndarray | None,
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray))
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if brightness < 14.0:
            reasons.append("frame appears too dark")
        if blur_score < 35.0:
            reasons.append("frame appears too blurry")

        if previous_frame is not None:
            delta = float(np.mean(cv2.absdiff(frame, previous_frame)))
            if delta > 35.0:
                reasons.append("frame transition appears abrupt")
        else:
            delta = 0.0

        candidate_count = len(candidates)
        confidence = self._mean(candidate.confidence for candidate in candidates)
        separation = self._candidate_separation_score(candidates, frame.shape[1], frame.shape[0])
        visibility = min(1.0, max(0.0, (brightness / 96.0) * 0.35 + min(1.0, blur_score / 120.0) * 0.35 + max(0.0, 1.0 - min(1.0, delta / 80.0)) * 0.30))
        score = (candidate_count * 10.0) + (confidence * 4.0) + (separation * 2.0) + (visibility * 2.0)
        if candidate_count == 0:
            reasons.append("no players detected on this sampled frame")
        return score, self._dedupe(reasons)

    def _candidate_separation_score(self, candidates: list[MatchPlayerCandidate], width: int, height: int) -> float:
        if len(candidates) < 2:
            return float(len(candidates))
        diagonal = max(1.0, (width**2 + height**2) ** 0.5)
        centers = [
            (candidate.box.x + candidate.box.width / 2.0, candidate.box.y + candidate.box.height / 2.0)
            for candidate in candidates
        ]
        distances: list[float] = []
        for index, (x1, y1) in enumerate(centers):
            for x2, y2 in centers[index + 1 :]:
                distances.append(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 / diagonal)
        return self._mean(distances)

    def _detect_candidates(self, frame: np.ndarray, frame_index: int) -> list[MatchPlayerCandidate]:
        candidates = self._detector.detect(frame)
        ordered = sorted(candidates, key=lambda candidate: (candidate.box.x, candidate.box.y))
        relabeled: list[MatchPlayerCandidate] = []
        for index, candidate in enumerate(ordered, start=1):
            relabeled.append(
                candidate.model_copy(
                    update={
                        "candidate_id": f"frame-{frame_index}-candidate-{index}",
                        "label": candidate.label or f"Player {index}",
                    }
                )
            )
        return relabeled

    def _preview_reasons(self, candidates: Iterable[MatchPlayerCandidate]) -> list[str]:
        reasons: list[str] = []
        candidate_list = list(candidates)
        if not candidate_list:
            reasons.append("no players detected on the preview frame")
        elif len(candidate_list) < 4:
            reasons.append("fewer than four players were visible in the selected preview frame")
        return self._dedupe(reasons)

    def _classify_tracking_confidence(self, coverage: float, average_confidence: float, missing_intervals: list[MatchTrackingInterval]) -> str:
        if coverage >= 0.9 and average_confidence >= 0.7 and len(missing_intervals) <= 2:
            return "high"
        if coverage >= 0.75 and average_confidence >= 0.55:
            return "medium"
        if coverage >= 0.55:
            return "low"
        return "insufficient"

    def _tracking_reasons(
        self,
        coverage: float,
        average_confidence: float,
        missing_intervals: list[MatchTrackingInterval],
    ) -> list[str]:
        reasons: list[str] = []
        if coverage < 0.75:
            reasons.append("selected player tracking covered fewer than 75% of frames")
        if average_confidence < 0.55:
            reasons.append("average tracker confidence was low")
        if missing_intervals:
            reasons.append("temporary occlusions were detected")
        return self._dedupe(reasons)

    def _classify_candidate_confidence(self, peak_value: float, baseline: float, stdev: float, track_confidence: float) -> float:
        prominence = max(0.0, peak_value - baseline)
        signal = 0.0 if stdev <= 0 else min(1.0, prominence / max(0.01, stdev * 2.0))
        value = 0.45 * signal + 0.35 * track_confidence + 0.20 * min(1.0, peak_value * 2.0)
        return round(max(0.0, min(1.0, value)), 4)

    def _find_peaks(self, values: list[float], *, threshold: float, min_gap: int) -> list[int]:
        peaks: list[int] = []
        index = 1
        last_peak = -min_gap
        while index < len(values) - 1:
            current = values[index]
            if current >= threshold and current >= values[index - 1] and current >= values[index + 1] and index - last_peak >= min_gap:
                peaks.append(index)
                last_peak = index
                index += min_gap
                continue
            index += 1
        return peaks

    def _dedupe_candidates(self, candidates: list[MatchStrokeCandidate]) -> list[MatchStrokeCandidate]:
        ordered: list[MatchStrokeCandidate] = []
        for candidate in candidates:
            if any(abs(candidate.peak_frame - other.peak_frame) < 6 for other in ordered):
                continue
            ordered.append(candidate)
        return ordered

    def _smooth_signal(self, values: list[float], window: int = 5) -> list[float]:
        if window <= 1 or len(values) <= 2:
            return list(values)
        padded = [values[0]] * (window // 2) + values + [values[-1]] * (window // 2)
        return [float(sum(padded[index : index + window]) / window) for index in range(len(values))]

    def _selected_candidate_id(self, job) -> str | None:
        source = getattr(job, "source", {}) or {}
        if not isinstance(source, dict):
            return None
        for key in ("selected_player_candidate_id", "selected_player_id"):
            value = str(source.get(key, "")).strip()
            if value:
                return value
        match = source.get("match")
        if isinstance(match, dict):
            value = str(match.get("selected_player_candidate_id", "")).strip()
            if value:
                return value
        return None

    def _probe(self, video_path: str) -> VideoProbe:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise MatchAnalysisError("match_video_open_failed", "Unable to open video file.")

        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)

        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()

        duration_seconds = float(frame_count / fps) if frame_count > 0 and fps > 0 else 0.0
        if width <= 0 or height <= 0:
            raise MatchAnalysisError("match_video_probe_failed", "Unable to determine video dimensions.")
        if frame_count <= 0:
            raise MatchAnalysisError("match_video_probe_failed", "Unable to determine video duration.")

        return VideoProbe(width=width, height=height, fps=fps, frame_count=frame_count, duration_seconds=duration_seconds)

    def _read_frame_at(self, video_path: str, frame_index: int) -> np.ndarray | None:
        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            return None
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
        ok, frame = capture.read()
        capture.release()
        return frame if ok and frame is not None else None

    def _track_crop(self, frame: np.ndarray, box: MatchBoundingBox | None, padding: float = 0.28) -> np.ndarray | None:
        if box is None:
            return None
        x, y, w, h = self._expanded_bounds(box, frame.shape[1], frame.shape[0], padding)
        crop = frame[y : y + h, x : x + w]
        if crop.size == 0:
            return None
        return crop

    def _crop_frame(self, frame: np.ndarray, box: MatchBoundingBox | None, padding: float = 0.28) -> np.ndarray | None:
        return self._track_crop(frame, box, padding=padding)

    def _expanded_bounds(self, box: MatchBoundingBox, width: int, height: int, padding: float) -> tuple[int, int, int, int]:
        pad_x = int(round(box.width * padding))
        pad_y = int(round(box.height * padding))
        x = max(0, box.x - pad_x)
        y = max(0, box.y - pad_y)
        x2 = min(width, box.x + box.width + pad_x)
        y2 = min(height, box.y + box.height + pad_y)
        return x, y, max(1, x2 - x), max(1, y2 - y)

    def _crop_filter(self, box: MatchBoundingBox, frame_width: int, frame_height: int, padding: float) -> str:
        x, y, w, h = self._expanded_bounds(box, frame_width, frame_height, padding)
        return f"crop={w}:{h}:{x}:{y}"

    def _average_box(self, frames: Iterable[MatchTrackFrame]) -> MatchBoundingBox | None:
        boxes = [frame.box for frame in frames if frame.box is not None]
        if not boxes:
            return None
        return MatchBoundingBox(
            x=int(round(sum(box.x for box in boxes) / len(boxes))),
            y=int(round(sum(box.y for box in boxes) / len(boxes))),
            width=max(1, int(round(sum(box.width for box in boxes) / len(boxes)))),
            height=max(1, int(round(sum(box.height for box in boxes) / len(boxes)))),
        )

    def _run(self, command: list[str], code: str) -> None:
        try:
            completed = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise MatchAnalysisError(code, f"Failed to execute command: {exc}") from exc
        if completed.returncode != 0:
            raise MatchAnalysisError(code, "Command execution failed.", {"return_code": completed.returncode, "command": command})

    def _ffmpeg_path(self) -> str:
        from shutil import which

        return which("ffmpeg") or ""

    def _artifact_reference(self, path: Path | None, mime_type: str, kind: str) -> PoseArtifactReference | None:
        if path is None or not path.exists():
            return None
        storage_key = str(path.resolve().relative_to(self._storage_root.resolve()))
        return PoseArtifactReference(
            type=kind,
            storage_key=storage_key,
            mime_type=mime_type,
            filename=path.name,
            size_bytes=path.stat().st_size,
        )

    def _artifact_root(self, job_id: str) -> Path:
        return self._storage_root / "match" / job_id

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

    def _dedupe(self, reasons: Iterable[str]) -> list[str]:
        unique: list[str] = []
        for reason in reasons:
            if reason and reason not in unique:
                unique.append(reason)
        return unique

    def _mean(self, values: Iterable[float]) -> float:
        items = [float(value) for value in values]
        return float(sum(items) / len(items)) if items else 0.0

    def _timestamp_ms(self, frame_index: int, fps: float) -> int:
        if fps <= 0:
            return 0
        return int(round((frame_index / fps) * 1000.0))
