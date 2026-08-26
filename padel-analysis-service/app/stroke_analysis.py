from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean, median

from .models import JobResponse
from .pose_models import POSE_LANDMARK_NAMES, PoseAnalysisSummary, PoseEstimationResult, PoseFrameRecord
from .stroke_geometry import (
    Point2D,
    clamp_unit,
    distance,
    ema,
    line_orientation,
    midpoint,
    normalize_angle_degrees,
    three_point_joint_angle,
    velocity,
)
from .stroke_models import (
    StrokeAnalysisCapabilitySummary,
    StrokeAnalysisSummary,
    StrokeAngleMeasurement,
    StrokeBalanceProxySummary,
    StrokeCapabilityAssessment,
    StrokeConfidenceLevel,
    StrokeContactFrame,
    StrokeHandedness,
    StrokeKneeAnglesAtContact,
    StrokeKneeCapabilitySummary,
    StrokeLandmarkQualitySummary,
    StrokeMetricsSummary,
    StrokePhaseEvent,
    StrokePhaseMetricTriple,
    StrokePhasesSummary,
    StrokeSmoothingSummary,
    StrokeWristVelocityProfile,
)


@dataclass(frozen=True, slots=True)
class StrokeAnalysisConfig:
    smoothing_alpha: float = 0.35
    min_pose_coverage: float = 0.45
    min_selected_visibility: float = 0.55
    min_consecutive_frames: int = 12
    min_peak_velocity: float = 0.02
    min_motion_range: float = 0.05
    prep_velocity_factor: float = 1.55
    baseline_window_frames: int = 10
    forward_window_frames: int = 18
    recovery_window_frames: int = 24
    min_post_follow_through_seconds: float = 2.0
    smoothing_visibility_threshold: float = 0.15
    capability_min_visibility: float = 0.55
    capability_min_usable_frame_percentage: float = 0.70
    capability_max_missing_gap_frames: int = 5
    capability_interpolation_gap_frames: int = 3


@dataclass(frozen=True, slots=True)
class StrokeProcessingError(RuntimeError):
    code: str
    message: str
    detail: dict[str, object] | list[object] | str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class StrokeFrameState:
    frame_index: int
    timestamp_ms: int
    reliable: bool
    selected_visibility: float
    raw: dict[str, Point2D]
    visibility: dict[str, float]
    smoothed: dict[str, Point2D]
    selected_speed: float | None = None
    selected_acceleration: float | None = None
    selected_extension: float | None = None
    selected_elbow_angle: float | None = None
    shoulder_orientation: float | None = None
    hip_orientation: float | None = None
    body_center: Point2D | None = None
    support_range: float | None = None
    left_knee_angle: float | None = None
    right_knee_angle: float | None = None


class ForehandStrokeAnalyzer:
    def __init__(self, config: StrokeAnalysisConfig | None = None) -> None:
        self._config = config or StrokeAnalysisConfig()

    def analyze(
        self,
        job: JobResponse,
        pose_result: PoseEstimationResult,
        pose_summary: PoseAnalysisSummary,
        manual_contact_frame_index: int | None = None,
    ) -> StrokeAnalysisSummary:
        try:
            dominant_hand = self._dominant_hand(job.source.get("dominant_hand"))
            shot_type = str(job.source.get("shot_type", "forehand")).strip().lower() or "forehand"
            if shot_type != "forehand":
                return self._unavailable(dominant_hand, ["unsupported shot type"])

            frames = self._build_frame_states(pose_result.frames, dominant_hand)
            if not frames:
                return self._unavailable(dominant_hand, ["no pose frames available"])

            self._bridge_short_gaps(frames)
            reasons = self._precondition_reasons(frames, pose_summary)
            run = self._longest_reliable_run(frames)
            capabilities = self._build_analysis_capabilities(
                run or frames,
                pose_summary,
                dominant_hand,
            )
            if reasons:
                return self._unavailable(dominant_hand, reasons, capabilities=capabilities)

            if len(run) < self._config.min_consecutive_frames:
                return self._unavailable(
                    dominant_hand,
                    ["not enough consecutive reliable pose frames"],
                    capabilities=capabilities,
                )

            self._derive_motion_signals(run, dominant_hand)
            reasons = self._signal_reasons(run, dominant_hand)
            if reasons:
                return self._unavailable(dominant_hand, reasons, capabilities=capabilities)

            phases, metrics = self._detect_phases_and_metrics(
                run,
                dominant_hand,
                pose_summary,
                manual_contact_frame_index=manual_contact_frame_index,
            )
            confidence_level = self._classify_confidence(phases, metrics, pose_summary, run)
            phases.confidence_level = confidence_level
            phases.available = True
            capabilities = self._build_analysis_capabilities(
                run,
                pose_summary,
                dominant_hand,
                phases=phases,
                metrics=metrics,
            )

            return StrokeAnalysisSummary(
                enabled=True,
                available=True,
                dominant_hand=dominant_hand,
                confidence_level=confidence_level,
                smoothing=StrokeSmoothingSummary(
                    enabled=True,
                    method="ema",
                    alpha=self._config.smoothing_alpha,
                    notes=[f"landmark smoothing uses EMA(alpha={self._config.smoothing_alpha:.2f})"],
                ),
                analysis_capabilities=capabilities,
                phases=phases,
                metrics=metrics,
                reasons=list(phases.reasons),
            )
        except StrokeProcessingError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StrokeProcessingError(
                code="stroke_processing_failed",
                message="Stroke analysis failed.",
                detail={"type": exc.__class__.__name__},
            ) from exc

    def _dominant_hand(self, raw_value: object) -> StrokeHandedness:
        return "left" if str(raw_value).strip().lower() == "left" else "right"

    def _unavailable(
        self,
        dominant_hand: StrokeHandedness,
        reasons: list[str],
        *,
        capabilities: StrokeAnalysisCapabilitySummary | None = None,
    ) -> StrokeAnalysisSummary:
        unique_reasons = self._dedupe(reasons)
        return StrokeAnalysisSummary(
            enabled=True,
            available=False,
            dominant_hand=dominant_hand,
            confidence_level="insufficient",
            smoothing=StrokeSmoothingSummary(
                enabled=True,
                method="ema",
                alpha=self._config.smoothing_alpha,
                notes=[f"landmark smoothing uses EMA(alpha={self._config.smoothing_alpha:.2f})"],
            ),
            analysis_capabilities=capabilities or StrokeAnalysisCapabilitySummary(),
            phases=StrokePhasesSummary(
                available=False,
                confidence_level="insufficient",
                reasons=unique_reasons,
            ),
            metrics=None,
            reasons=unique_reasons,
        )

    def _bridge_short_gaps(self, frames: list[StrokeFrameState]) -> None:
        gap_start: int | None = None
        for index, frame in enumerate(frames):
            if frame.reliable:
                if gap_start is not None:
                    gap_length = index - gap_start
                    if gap_length <= self._config.capability_interpolation_gap_frames:
                        for gap_frame in frames[gap_start:index]:
                            gap_frame.reliable = True
                gap_start = None
                continue
            if gap_start is None:
                gap_start = index

    def _build_analysis_capabilities(
        self,
        frames: list[StrokeFrameState],
        pose_summary: PoseAnalysisSummary,
        dominant_hand: StrokeHandedness,
        *,
        phases: StrokePhasesSummary | None = None,
        metrics: StrokeMetricsSummary | None = None,
    ) -> StrokeAnalysisCapabilitySummary:
        window_frames = self._capability_window_frames(frames, phases)
        stroke_phases = self._assess_stroke_phases(window_frames, pose_summary, dominant_hand, phases=phases)
        elbow_angles = self._assess_elbow_angles(window_frames, dominant_hand, metrics=metrics)
        shoulder_rotation = self._assess_line_rotation(
            window_frames,
            "shoulder_rotation",
            ["left_shoulder", "right_shoulder"],
            phase_metric=metrics.shoulder_line_orientation if metrics is not None else None,
        )
        hip_rotation = self._assess_line_rotation(
            window_frames,
            "hip_rotation",
            ["left_hip", "right_hip"],
            phase_metric=metrics.hip_line_orientation if metrics is not None else None,
        )
        knee_angles = StrokeKneeCapabilitySummary(
            left=self._assess_knee_side(
                window_frames,
                "left",
                phase_measurement=metrics.knee_angles_at_contact.left if metrics is not None else None,
            ),
            right=self._assess_knee_side(
                window_frames,
                "right",
                phase_measurement=metrics.knee_angles_at_contact.right if metrics is not None else None,
            ),
        )
        balance = self._assess_balance(window_frames, metrics=metrics)
        recovery = self._assess_recovery(window_frames, pose_summary, phases=phases, metrics=metrics)
        return StrokeAnalysisCapabilitySummary(
            stroke_phases=stroke_phases,
            elbow_angles=elbow_angles,
            shoulder_rotation=shoulder_rotation,
            hip_rotation=hip_rotation,
            knee_angles=knee_angles,
            balance=balance,
            recovery=recovery,
        )

    def _capability_window_frames(
        self,
        frames: list[StrokeFrameState],
        phases: StrokePhasesSummary | None,
    ) -> list[StrokeFrameState]:
        if phases is None or not phases.available:
            return frames

        phase_indices = [
            event.frame_index
            for event in (
                phases.ready,
                phases.preparation_start,
                phases.backswing_end,
                phases.contact_estimate,
                phases.follow_through_peak,
                phases.recovery,
            )
            if event is not None and event.frame_index is not None
        ]
        if not phase_indices:
            return frames

        start_index = max(0, min(phase_indices) - 2)
        end_index = max(phase_indices) + 2
        return [frame for frame in frames if start_index <= frame.frame_index <= end_index]

    def _assess_stroke_phases(
        self,
        frames: list[StrokeFrameState],
        pose_summary: PoseAnalysisSummary,
        dominant_hand: StrokeHandedness,
        *,
        phases: StrokePhasesSummary | None,
    ) -> StrokeCapabilityAssessment:
        opposite_shoulder = "left_shoulder" if dominant_hand == "right" else "right_shoulder"
        required_landmarks = [
            f"{dominant_hand}_shoulder",
            f"{dominant_hand}_elbow",
            f"{dominant_hand}_wrist",
            opposite_shoulder,
            "left_hip",
            "right_hip",
        ]
        warnings: list[str] = []
        if pose_summary.full_body_visible is False:
            warnings.append("full body not consistently visible")
        if pose_summary.visibility.ankles < self._config.capability_min_visibility:
            warnings.append("feet not consistently visible")

        assessment = self._landmark_capability_assessment(
            frames,
            required_landmarks,
            capability_name="stroke_phases",
            warnings=warnings,
            available_override=phases is not None and phases.available,
        )
        if phases is not None:
            assessment.available = phases.available
            assessment.confidence_level = phases.confidence_level if phases.available else "insufficient"
            if phases.reasons and not assessment.available:
                assessment.reasons = self._dedupe([*assessment.reasons, *phases.reasons])
        return assessment

    def _assess_elbow_angles(
        self,
        frames: list[StrokeFrameState],
        dominant_hand: StrokeHandedness,
        *,
        metrics: StrokeMetricsSummary | None,
    ) -> StrokeCapabilityAssessment:
        required_landmarks = [
            f"{dominant_hand}_shoulder",
            f"{dominant_hand}_elbow",
            f"{dominant_hand}_wrist",
        ]
        available_override = False
        if metrics is not None:
            available_override = any(
                measurement is not None and measurement.available
                for measurement in (
                    metrics.dominant_elbow_angle.preparation,
                    metrics.dominant_elbow_angle.contact,
                    metrics.dominant_elbow_angle.follow_through,
                )
            )
        return self._landmark_capability_assessment(
            frames,
            required_landmarks,
            capability_name="elbow_angles",
            available_override=available_override,
        )

    def _assess_line_rotation(
        self,
        frames: list[StrokeFrameState],
        capability_name: str,
        required_landmarks: list[str],
        *,
        phase_metric: StrokePhaseMetricTriple | None,
    ) -> StrokeCapabilityAssessment:
        available_override = any(
            measurement is not None and measurement.available
            for measurement in (
                phase_metric.preparation if phase_metric is not None else None,
                phase_metric.contact if phase_metric is not None else None,
                phase_metric.follow_through if phase_metric is not None else None,
            )
        )
        assessment = self._landmark_capability_assessment(
            frames,
            required_landmarks,
            capability_name=capability_name,
            available_override=available_override,
        )
        if phase_metric is not None and assessment.available and assessment.confidence_level == "insufficient":
            assessment.confidence_level = self._capability_confidence(assessment.diagnostics)
        return assessment

    def _assess_knee_side(
        self,
        frames: list[StrokeFrameState],
        side: str,
        *,
        phase_measurement: StrokeAngleMeasurement | None,
    ) -> StrokeCapabilityAssessment:
        required_landmarks = [f"{side}_hip", f"{side}_knee", f"{side}_ankle"]
        assessment = self._landmark_capability_assessment(
            frames,
            required_landmarks,
            capability_name=f"{side}_knee_angle",
            available_override=phase_measurement is not None and phase_measurement.available,
        )
        if phase_measurement is None or not phase_measurement.available:
            assessment.reasons = self._dedupe([*assessment.reasons, f"{side} knee angle unavailable"])
        return assessment

    def _assess_balance(
        self,
        frames: list[StrokeFrameState],
        *,
        metrics: StrokeMetricsSummary | None,
    ) -> StrokeCapabilityAssessment:
        required_landmarks = [
            "left_hip",
            "right_hip",
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
        ]
        assessment = self._landmark_capability_assessment(
            frames,
            required_landmarks,
            capability_name="balance",
            available_override=metrics is not None and metrics.balance_proxy.reliable,
        )
        if metrics is not None and metrics.balance_proxy.reasons:
            assessment.reasons = self._dedupe([*assessment.reasons, *metrics.balance_proxy.reasons])
        return assessment

    def _assess_recovery(
        self,
        frames: list[StrokeFrameState],
        pose_summary: PoseAnalysisSummary,
        *,
        phases: StrokePhasesSummary | None,
        metrics: StrokeMetricsSummary | None,
    ) -> StrokeCapabilityAssessment:
        required_landmarks = [
            "left_shoulder",
            "right_shoulder",
            "left_hip",
            "right_hip",
            "left_wrist",
            "right_wrist",
        ]
        available_override = phases is not None and phases.recovery is not None and metrics is not None and metrics.recovery_duration_ms is not None
        assessment = self._landmark_capability_assessment(
            frames,
            required_landmarks,
            capability_name="recovery",
            available_override=available_override,
            warnings=["recovery needs sufficient post-follow-through footage"] if pose_summary.processed_frames > 0 else [],
        )
        if phases is not None and phases.reasons and not assessment.available:
            assessment.reasons = self._dedupe([*assessment.reasons, *phases.reasons])
        if metrics is not None and metrics.recovery_duration_ms is None and not assessment.available:
            assessment.reasons = self._dedupe([*assessment.reasons, "recovery phase not detected reliably"])
        return assessment

    def _landmark_capability_assessment(
        self,
        frames: list[StrokeFrameState],
        required_landmarks: list[str],
        *,
        capability_name: str,
        warnings: list[str] | None = None,
        available_override: bool | None = None,
    ) -> StrokeCapabilityAssessment:
        diagnostics = [
            self._landmark_quality_summary(frame_records=frames, landmark_name=landmark_name)
            for landmark_name in required_landmarks
        ]
        quality_available = all(
            self._landmark_quality_meets_thresholds(diagnostic)
            for diagnostic in diagnostics
        )
        available = quality_available if available_override is None else (quality_available and available_override)
        confidence_level = self._capability_confidence(diagnostics) if available else "insufficient"
        reasons = self._capability_reasons(capability_name, diagnostics, available)
        return StrokeCapabilityAssessment(
            available=available,
            confidence_level=confidence_level,
            required_landmarks=required_landmarks,
            warnings=self._dedupe(warnings or []),
            reasons=reasons,
            diagnostics=diagnostics,
        )

    def _landmark_quality_summary(
        self,
        frame_records: list[StrokeFrameState],
        *,
        landmark_name: str,
    ) -> StrokeLandmarkQualitySummary:
        visibilities: list[float] = []
        usable_count = 0
        gap_lengths: list[int] = []
        current_gap = 0
        interpolated_frames = 0
        seen_usable = False

        for frame in frame_records:
            visibility = float(frame.visibility.get(landmark_name, 0.0))
            visibilities.append(visibility)
            if visibility >= self._config.capability_min_visibility:
                usable_count += 1
                if current_gap > 0:
                    gap_lengths.append(current_gap)
                    if seen_usable and current_gap <= self._config.capability_interpolation_gap_frames:
                        interpolated_frames += current_gap
                    current_gap = 0
                seen_usable = True
            else:
                current_gap += 1

        if current_gap > 0:
            gap_lengths.append(current_gap)

        frame_count = len(visibilities)
        median_visibility = float(median(visibilities)) if visibilities else None
        lower_percentile_visibility = self._percentile(visibilities, 25) if visibilities else None
        usable_frame_percentage = (
            float((usable_count + interpolated_frames) / frame_count) if frame_count > 0 else None
        )
        longest_missing_gap_frames = max(gap_lengths) if gap_lengths else 0
        motion_continuity = 1.0 - (longest_missing_gap_frames / frame_count) if frame_count > 0 else None
        return StrokeLandmarkQualitySummary(
            landmark_name=landmark_name,
            frame_count=frame_count,
            median_visibility=round(median_visibility, 4) if median_visibility is not None else None,
            lower_percentile_visibility=round(lower_percentile_visibility, 4) if lower_percentile_visibility is not None else None,
            usable_frame_percentage=round(usable_frame_percentage, 4) if usable_frame_percentage is not None else None,
            longest_missing_gap_frames=longest_missing_gap_frames,
            interpolated_frames=interpolated_frames,
            motion_continuity=round(motion_continuity, 4) if motion_continuity is not None else None,
            required_visibility=self._config.capability_min_visibility,
            required_usable_frame_percentage=self._config.capability_min_usable_frame_percentage,
            allowed_gap_frames=self._config.capability_max_missing_gap_frames,
        )

    def _landmark_quality_meets_thresholds(self, diagnostic: StrokeLandmarkQualitySummary) -> bool:
        if diagnostic.frame_count <= 0:
            return False
        if diagnostic.median_visibility is None or diagnostic.usable_frame_percentage is None:
            return False
        return (
            diagnostic.median_visibility >= self._config.capability_min_visibility
            and diagnostic.usable_frame_percentage >= self._config.capability_min_usable_frame_percentage
            and diagnostic.longest_missing_gap_frames <= self._config.capability_max_missing_gap_frames
        )

    def _capability_confidence(self, diagnostics: list[StrokeLandmarkQualitySummary]) -> StrokeConfidenceLevel:
        if not diagnostics:
            return "insufficient"
        if not all(self._landmark_quality_meets_thresholds(diagnostic) for diagnostic in diagnostics):
            return "insufficient"

        minimum_median = min(diagnostic.median_visibility or 0.0 for diagnostic in diagnostics)
        minimum_usable = min(diagnostic.usable_frame_percentage or 0.0 for diagnostic in diagnostics)
        maximum_gap = max(diagnostic.longest_missing_gap_frames for diagnostic in diagnostics)
        if minimum_median >= 0.80 and minimum_usable >= 0.90 and maximum_gap <= 2:
            return "high"
        if minimum_median >= 0.65 and minimum_usable >= 0.80 and maximum_gap <= 4:
            return "medium"
        return "low"

    def _capability_reasons(
        self,
        capability_name: str,
        diagnostics: list[StrokeLandmarkQualitySummary],
        available: bool,
    ) -> list[str]:
        if available:
            return []
        reasons: list[str] = []
        for diagnostic in diagnostics:
            if diagnostic.frame_count <= 0:
                reasons.append(f"{capability_name}: {diagnostic.landmark_name} has no usable frames")
                continue
            if diagnostic.median_visibility is None or diagnostic.usable_frame_percentage is None:
                reasons.append(f"{capability_name}: {diagnostic.landmark_name} visibility statistics unavailable")
                continue
            if diagnostic.median_visibility < self._config.capability_min_visibility:
                reasons.append(
                    f"{capability_name}: {diagnostic.landmark_name} median visibility {diagnostic.median_visibility:.2f} "
                    f"< {self._config.capability_min_visibility:.2f}"
                )
            if diagnostic.usable_frame_percentage < self._config.capability_min_usable_frame_percentage:
                reasons.append(
                    f"{capability_name}: {diagnostic.landmark_name} usable-frame percentage {diagnostic.usable_frame_percentage:.2f} "
                    f"< {self._config.capability_min_usable_frame_percentage:.2f}"
                )
            if diagnostic.longest_missing_gap_frames > self._config.capability_max_missing_gap_frames:
                reasons.append(
                    f"{capability_name}: {diagnostic.landmark_name} longest missing gap {diagnostic.longest_missing_gap_frames} "
                    f"> {self._config.capability_max_missing_gap_frames}"
                )
        return self._dedupe(reasons)

    def _percentile(self, values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return float(ordered[0])
        rank = (percentile / 100.0) * (len(ordered) - 1)
        lower = int(rank)
        upper = min(len(ordered) - 1, lower + 1)
        if lower == upper:
            return float(ordered[lower])
        weight = rank - lower
        return float(ordered[lower] + ((ordered[upper] - ordered[lower]) * weight))

    def _build_frame_states(self, frames: Iterable[PoseFrameRecord], dominant_hand: StrokeHandedness) -> list[StrokeFrameState]:
        sequence: list[StrokeFrameState] = []
        history: dict[str, Point2D] = {}
        previous_frame_index: int | None = None

        for frame in frames:
            raw_points: dict[str, Point2D] = {}
            visibilities: dict[str, float] = {}
            smoothed_points = dict(history)
            for landmark in frame.landmarks:
                if landmark.landmark_name not in POSE_LANDMARK_NAMES:
                    continue
                point = Point2D(landmark.x, landmark.y)
                raw_points[landmark.landmark_name] = point
                visibility = float(landmark.visibility) if landmark.visibility is not None else 0.0
                visibilities[landmark.landmark_name] = visibility
                if visibility >= self._config.smoothing_visibility_threshold:
                    smoothed_points[landmark.landmark_name] = ema(
                        history.get(landmark.landmark_name),
                        point,
                        self._config.smoothing_alpha,
                    )

            history = smoothed_points
            selected_visibility = self._selected_visibility(frame, dominant_hand)
            reliable = (
                selected_visibility >= self._config.min_selected_visibility
                and (previous_frame_index is None or frame.frame_index == previous_frame_index + 1)
            )
            previous_frame_index = frame.frame_index

            state = StrokeFrameState(
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                reliable=reliable,
                selected_visibility=selected_visibility,
                raw=raw_points,
                visibility=visibilities,
                smoothed=smoothed_points,
            )
            self._attach_static_metrics(state, dominant_hand)
            sequence.append(state)

        return sequence

    def _attach_static_metrics(self, state: StrokeFrameState, dominant_hand: StrokeHandedness) -> None:
        smoothed = state.smoothed
        selected = dominant_hand

        shoulder = smoothed.get(f"{selected}_shoulder")
        elbow = smoothed.get(f"{selected}_elbow")
        wrist = smoothed.get(f"{selected}_wrist")
        if shoulder and elbow and wrist:
            state.selected_elbow_angle = three_point_joint_angle(shoulder, elbow, wrist)
            torso_scale = self._torso_scale(smoothed)
            state.selected_extension = distance(wrist, shoulder) / torso_scale if torso_scale > 0 else None

        left_shoulder = smoothed.get("left_shoulder")
        right_shoulder = smoothed.get("right_shoulder")
        if left_shoulder and right_shoulder:
            state.shoulder_orientation = line_orientation(left_shoulder, right_shoulder)

        left_hip = smoothed.get("left_hip")
        right_hip = smoothed.get("right_hip")
        if left_hip and right_hip:
            state.hip_orientation = line_orientation(left_hip, right_hip)

        if smoothed.get("left_hip") and smoothed.get("left_knee") and smoothed.get("left_ankle"):
            state.left_knee_angle = three_point_joint_angle(smoothed["left_hip"], smoothed["left_knee"], smoothed["left_ankle"])
        if smoothed.get("right_hip") and smoothed.get("right_knee") and smoothed.get("right_ankle"):
            state.right_knee_angle = three_point_joint_angle(smoothed["right_hip"], smoothed["right_knee"], smoothed["right_ankle"])

        state.body_center = self._body_center(smoothed)
        state.support_range = self._support_range(smoothed)

        if state.selected_extension is None and shoulder and elbow and wrist:
            state.selected_extension = distance(wrist, shoulder) / self._torso_scale(smoothed)

        if state.selected_elbow_angle is None and shoulder and elbow and wrist:
            state.selected_elbow_angle = three_point_joint_angle(shoulder, elbow, wrist)

    def _derive_motion_signals(self, frames: list[StrokeFrameState], dominant_hand: StrokeHandedness) -> None:
        previous_frame: StrokeFrameState | None = None
        previous_speed: float | None = None
        for frame in frames:
            wrist = frame.smoothed.get(f"{dominant_hand}_wrist")
            speed: float | None = None
            acceleration: float | None = None
            if previous_frame is not None and previous_frame.reliable and frame.reliable:
                previous_wrist = previous_frame.smoothed.get(f"{dominant_hand}_wrist")
                if previous_wrist is not None and wrist is not None:
                    speed = velocity(previous_wrist, wrist, previous_frame.timestamp_ms, frame.timestamp_ms)
                    if speed is not None and previous_speed is not None:
                        delta_seconds = (frame.timestamp_ms - previous_frame.timestamp_ms) / 1000.0
                        if delta_seconds > 0:
                            acceleration = (speed - previous_speed) / delta_seconds
            frame.selected_speed = speed
            frame.selected_acceleration = acceleration
            if speed is not None:
                previous_speed = speed
            previous_frame = frame

    def _precondition_reasons(self, frames: list[StrokeFrameState], pose_summary: PoseAnalysisSummary) -> list[str]:
        reasons: list[str] = []
        if pose_summary.pose_coverage < self._config.min_pose_coverage:
            reasons.append("pose coverage too low")

        reliable_frames = [frame for frame in frames if frame.reliable]
        if not reliable_frames:
            reasons.append("dominant wrist, elbow, and shoulder visibility too low")
        if len(self._longest_reliable_run(frames)) < self._config.min_consecutive_frames:
            reasons.append("not enough consecutive reliable pose frames")

        reliable_timestamps = [frame.timestamp_ms for frame in reliable_frames]
        if len(reliable_timestamps) >= 2 and any(next_timestamp <= timestamp for timestamp, next_timestamp in zip(reliable_timestamps, reliable_timestamps[1:])):
            reasons.append("timestamps are not strictly increasing")
        return self._dedupe(reasons)

    def _longest_reliable_run(self, frames: list[StrokeFrameState]) -> list[StrokeFrameState]:
        best: list[StrokeFrameState] = []
        current: list[StrokeFrameState] = []
        for frame in frames:
            if frame.reliable:
                current.append(frame)
                continue
            if len(current) > len(best):
                best = current
            current = []
        if len(current) > len(best):
            best = current
        return best

    def _signal_reasons(self, frames: list[StrokeFrameState], dominant_hand: StrokeHandedness) -> list[str]:
        reasons: list[str] = []
        speeds = [frame.selected_speed for frame in frames if frame.selected_speed is not None]
        if not speeds:
            return ["meaningful movement not detected"]

        peak_speed = max(speeds)
        path_length = 0.0
        for previous, current in zip(frames, frames[1:]):
            previous_wrist = previous.smoothed.get(f"{dominant_hand}_wrist")
            current_wrist = current.smoothed.get(f"{dominant_hand}_wrist")
            if previous_wrist is None or current_wrist is None:
                continue
            path_length += distance(previous_wrist, current_wrist)

        if peak_speed < self._config.min_peak_velocity:
            reasons.append("meaningful movement not detected")
        if path_length < self._config.min_motion_range:
            reasons.append("dominant wrist movement range too small")
        return self._dedupe(reasons)

    def _detect_phases_and_metrics(
        self,
        frames: list[StrokeFrameState],
        dominant_hand: StrokeHandedness,
        pose_summary: PoseAnalysisSummary,
        manual_contact_frame_index: int | None = None,
    ) -> tuple[StrokePhasesSummary, StrokeMetricsSummary]:
        speeds = [frame.selected_speed or 0.0 for frame in frames]
        extensions = [frame.selected_extension or 0.0 for frame in frames]
        angles = [frame.selected_elbow_angle or 180.0 for frame in frames]
        peak_speed = max(speeds)
        peak_speed_index = speeds.index(peak_speed)

        baseline_count = min(len(frames), max(3, min(self._config.baseline_window_frames, max(3, len(frames) // 6))))
        baseline_speed = median(speeds[:baseline_count]) if baseline_count > 0 else 0.0
        extension_max = max(extensions) if any(extensions) else 1.0
        angle_max = max(angles) if any(angles) else 180.0

        prep_threshold = max(baseline_speed * self._config.prep_velocity_factor, baseline_speed + 0.005)
        prep_index = self._find_preparation_start(frames, speeds, extensions, prep_threshold, peak_speed_index)
        ready_index = 0
        backswing_index = self._find_backswing_end(frames, prep_index, peak_speed_index)
        auto_contact_index = self._find_contact_index(frames, peak_speed_index, backswing_index, extension_max, angle_max, peak_speed)
        contact_index = self._resolve_contact_index(frames, manual_contact_frame_index, auto_contact_index)
        follow_index = self._find_follow_through_peak(frames, contact_index, extension_max, peak_speed)
        recovery_index = self._find_recovery_index(frames, follow_index, baseline_speed, extension_max)

        phases = StrokePhasesSummary(
            available=True,
            confidence_level="low",
            ready=self._event_from_frame(frames[ready_index], ["first reliable pose frame"]),
            preparation_start=self._event_from_frame(
                frames[prep_index],
                ["dominant wrist velocity rose above the baseline", "selected arm extension began increasing"],
            ),
            backswing_end=self._event_from_frame(
                frames[backswing_index],
                ["selected elbow angle reached a local minimum", "wrist motion slowed before the forward swing"],
            ),
            contact_estimate=self._contact_event(
                frames,
                contact_index,
                peak_speed,
                extension_max,
                pose_summary,
                source="manual" if manual_contact_frame_index is not None else "estimated",
                candidate_index=auto_contact_index,
                evidence=[
                    "dominant wrist velocity peaked",
                    "selected arm extension was high during the forward swing",
                ],
            ),
            original_contact_estimate=(
                self._contact_event(
                    frames,
                    auto_contact_index,
                    peak_speed,
                    extension_max,
                    pose_summary,
                    source="estimated",
                    candidate_index=auto_contact_index,
                    evidence=[
                        "dominant wrist velocity peaked",
                        "selected arm extension was high during the forward swing",
                    ],
                )
                if manual_contact_frame_index is not None
                else None
            ),
            follow_through_peak=self._event_from_frame(
                frames[follow_index],
                ["selected wrist stayed extended after contact", "forward swing carried across the body"],
            ),
            recovery=(
                self._event_from_frame(
                    frames[recovery_index],
                    ["selected wrist velocity returned near the baseline", "body center stabilized after contact"],
                )
                if recovery_index is not None
                else None
            ),
            reasons=self._recovery_reasons(frames, follow_index, recovery_index),
        )

        metrics = StrokeMetricsSummary(
            dominant_elbow_angle=self._phase_metric_triple(frames, prep_index, contact_index, follow_index, metric="elbow"),
            shoulder_line_orientation=self._phase_metric_triple(frames, prep_index, contact_index, follow_index, metric="shoulder"),
            hip_line_orientation=self._phase_metric_triple(frames, prep_index, contact_index, follow_index, metric="hip"),
            knee_angles_at_contact=self._knee_angles_at_contact(frames[contact_index]),
            wrist_velocity_profile=self._wrist_velocity_profile(frames, contact_index, peak_speed_index),
            recovery_duration_ms=self._recovery_duration_ms(frames, follow_index, recovery_index),
            balance_proxy=self._balance_proxy(frames, contact_index, recovery_index),
        )
        return phases, metrics

    def _find_preparation_start(
        self,
        frames: list[StrokeFrameState],
        speeds: list[float],
        extensions: list[float],
        threshold: float,
        peak_speed_index: int,
    ) -> int:
        for index in range(1, max(1, peak_speed_index + 1)):
            if speeds[index] < threshold:
                continue
            if extensions[index] < extensions[index - 1]:
                continue
            if speeds[index] < speeds[index - 1]:
                continue
            return index
        return max(0, peak_speed_index // 2)

    def _find_backswing_end(self, frames: list[StrokeFrameState], prep_index: int, peak_speed_index: int) -> int:
        start = min(prep_index, peak_speed_index)
        end = max(prep_index + 1, peak_speed_index)
        best_index = start
        best_angle = float("inf")
        for index in range(start, end + 1):
            angle = frames[index].selected_elbow_angle
            if angle is None or angle >= best_angle:
                continue
            best_angle = angle
            best_index = index
        return best_index

    def _find_contact_index(
        self,
        frames: list[StrokeFrameState],
        peak_speed_index: int,
        backswing_index: int,
        extension_max: float,
        angle_max: float,
        peak_speed: float,
    ) -> int:
        start = max(backswing_index, peak_speed_index - 4)
        end = min(len(frames) - 1, peak_speed_index + 4)
        best_index = peak_speed_index
        best_score = float("-inf")
        for index in range(start, end + 1):
            score = self._stroke_score(frames[index], extension_max, angle_max, peak_speed)
            if score > best_score:
                best_score = score
                best_index = index
        return best_index

    def _resolve_contact_index(
        self,
        frames: list[StrokeFrameState],
        manual_contact_frame_index: int | None,
        auto_contact_index: int,
    ) -> int:
        if manual_contact_frame_index is None:
            return auto_contact_index

        for index, frame in enumerate(frames):
            if frame.frame_index == manual_contact_frame_index:
                return index
        return auto_contact_index

    def _find_follow_through_peak(
        self,
        frames: list[StrokeFrameState],
        contact_index: int,
        extension_max: float,
        peak_speed: float,
    ) -> int:
        start = min(len(frames) - 1, contact_index + 1)
        end = min(len(frames) - 1, contact_index + self._config.forward_window_frames)
        best_index = start
        best_score = float("-inf")
        for index in range(start, end + 1):
            frame = frames[index]
            extension = frame.selected_extension or 0.0
            speed = frame.selected_speed or 0.0
            extension_score = extension / extension_max if extension_max > 0 else 0.0
            speed_score = speed / peak_speed if peak_speed > 0 else 0.0
            score = (extension_score * 0.7) + (speed_score * 0.3)
            if score > best_score:
                best_score = score
                best_index = index
        return best_index

    def _find_recovery_index(
        self,
        frames: list[StrokeFrameState],
        follow_index: int,
        baseline_speed: float,
        extension_max: float,
    ) -> int | None:
        if self._insufficient_post_follow_through_footage(frames, follow_index):
            return None
        start = min(len(frames) - 1, follow_index + 1)
        end = min(len(frames) - 1, follow_index + self._config.recovery_window_frames)
        if start >= len(frames):
            return None
        for index in range(start, end + 1):
            frame = frames[index]
            speed = frame.selected_speed or 0.0
            extension = frame.selected_extension or 0.0
            if speed <= max(baseline_speed * 1.2, baseline_speed + 0.003) and extension <= extension_max * 0.95:
                return index
        return None

    def _insufficient_post_follow_through_footage(self, frames: list[StrokeFrameState], follow_index: int) -> bool:
        if not frames or follow_index >= len(frames) - 1:
            return True
        follow_frame = frames[follow_index]
        last_frame = frames[-1]
        return (last_frame.timestamp_ms - follow_frame.timestamp_ms) < int(self._config.min_post_follow_through_seconds * 1000)

    def _phase_metric_triple(
        self,
        frames: list[StrokeFrameState],
        prep_index: int,
        contact_index: int,
        follow_index: int,
        *,
        metric: str,
    ) -> StrokePhaseMetricTriple:
        return StrokePhaseMetricTriple(
            preparation=self._metric_measurement(frames[prep_index], metric),
            contact=self._metric_measurement(frames[contact_index], metric),
            follow_through=self._metric_measurement(frames[follow_index], metric),
        )

    def _metric_measurement(self, frame: StrokeFrameState, metric: str) -> StrokeAngleMeasurement:
        evidence = [f"frame {frame.frame_index}", "smoothed pose landmarks"]
        if metric == "elbow":
            if frame.selected_elbow_angle is None:
                return StrokeAngleMeasurement(available=False, evidence=evidence)
            return StrokeAngleMeasurement(
                available=True,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                value_degrees=round(frame.selected_elbow_angle, 4),
                raw_value_degrees=round(frame.selected_elbow_angle, 4),
                evidence=evidence + ["selected shoulder-elbow-wrist triangle"],
            )
        if metric == "shoulder":
            if frame.shoulder_orientation is None:
                return StrokeAngleMeasurement(available=False, evidence=evidence)
            return StrokeAngleMeasurement(
                available=True,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                value_degrees=round(normalize_angle_degrees(frame.shoulder_orientation), 4),
                raw_value_degrees=round(frame.shoulder_orientation, 4),
                evidence=evidence + ["shoulder line orientation is an image-plane proxy"],
            )
        if metric == "hip":
            if frame.hip_orientation is None:
                return StrokeAngleMeasurement(available=False, evidence=evidence)
            return StrokeAngleMeasurement(
                available=True,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                value_degrees=round(normalize_angle_degrees(frame.hip_orientation), 4),
                raw_value_degrees=round(frame.hip_orientation, 4),
                evidence=evidence + ["hip line orientation is an image-plane proxy"],
            )
        raise StrokeProcessingError(code="stroke_processing_failed", message=f"Unsupported metric: {metric}")

    def _knee_angles_at_contact(self, frame: StrokeFrameState) -> StrokeKneeAnglesAtContact:
        if frame.left_knee_angle is not None:
            left = StrokeAngleMeasurement(
                available=True,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                value_degrees=round(frame.left_knee_angle, 4),
                evidence=["left hip-knee-ankle triangle"],
            )
        else:
            left = StrokeAngleMeasurement(available=False, evidence=["left knee not visible reliably"])

        if frame.right_knee_angle is not None:
            right = StrokeAngleMeasurement(
                available=True,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                value_degrees=round(frame.right_knee_angle, 4),
                evidence=["right hip-knee-ankle triangle"],
            )
        else:
            right = StrokeAngleMeasurement(available=False, evidence=["right knee not visible reliably"])
        return StrokeKneeAnglesAtContact(left=left, right=right)

    def _wrist_velocity_profile(
        self,
        frames: list[StrokeFrameState],
        contact_index: int,
        peak_speed_index: int,
    ) -> StrokeWristVelocityProfile:
        speeds = [frame.selected_speed or 0.0 for frame in frames]
        accelerations = [frame.selected_acceleration for frame in frames if frame.selected_acceleration is not None]
        peak_acceleration = max(accelerations) if accelerations else None
        peak_acceleration_frame_index = None
        if peak_acceleration is not None:
            for frame in frames:
                if frame.selected_acceleration == peak_acceleration:
                    peak_acceleration_frame_index = frame.frame_index
                    break
        return StrokeWristVelocityProfile(
            peak_normalized_velocity=round(speeds[peak_speed_index], 4),
            peak_frame_index=frames[peak_speed_index].frame_index,
            peak_timestamp_ms=frames[peak_speed_index].timestamp_ms,
            velocity_at_contact=round(speeds[contact_index], 4),
            peak_acceleration=round(peak_acceleration, 4) if peak_acceleration is not None else None,
            peak_acceleration_frame_index=peak_acceleration_frame_index,
            reliable=True,
            evidence=["selected wrist velocity from smoothed landmarks"],
        )

    def _contact_event(
        self,
        frames: list[StrokeFrameState],
        contact_index: int,
        peak_speed: float,
        extension_max: float,
        pose_summary: PoseAnalysisSummary,
        *,
        source: str,
        candidate_index: int,
        evidence: list[str],
    ) -> StrokeContactFrame:
        frame = frames[contact_index]
        return StrokeContactFrame(
            frame_index=frame.frame_index,
            timestamp_ms=frame.timestamp_ms,
            confidence=self._contact_confidence(frame, peak_speed, extension_max, pose_summary),
            evidence=evidence,
            source=source,  # type: ignore[arg-type]
            candidate_start_frame=frames[max(0, candidate_index - 6)].frame_index,
            candidate_end_frame=frames[min(len(frames) - 1, candidate_index + 6)].frame_index,
        )

    def _recovery_duration_ms(self, frames: list[StrokeFrameState], follow_index: int, recovery_index: int | None) -> int | None:
        if recovery_index is None:
            return None
        return max(0, frames[recovery_index].timestamp_ms - frames[follow_index].timestamp_ms)

    def _recovery_reasons(
        self,
        frames: list[StrokeFrameState],
        follow_index: int,
        recovery_index: int | None,
    ) -> list[str]:
        if recovery_index is not None:
            return []
        if self._insufficient_post_follow_through_footage(frames, follow_index):
            return ["insufficient_post_follow_through_footage"]
        return ["recovery phase not detected reliably"]

    def _balance_proxy(self, frames: list[StrokeFrameState], contact_index: int, recovery_index: int | None) -> StrokeBalanceProxySummary:
        support_range = frames[contact_index].support_range
        if support_range is None:
            return StrokeBalanceProxySummary(
                proxy_score=None,
                body_center_drift=None,
                foot_support_range=None,
                reliable=False,
                reasons=["foot support range not visible reliably"],
            )

        contact_center = frames[contact_index].body_center
        if contact_center is None:
            return StrokeBalanceProxySummary(
                proxy_score=None,
                body_center_drift=None,
                foot_support_range=round(support_range, 4),
                reliable=False,
                reasons=["body center not visible reliably"],
            )

        if recovery_index is None:
            return StrokeBalanceProxySummary(
                proxy_score=None,
                body_center_drift=None,
                foot_support_range=round(support_range, 4),
                reliable=False,
                reasons=["insufficient_post_follow_through_footage"],
            )

        recovery_points = [
            frame.body_center
            for frame in frames[contact_index + 1 : recovery_index + 1]
            if frame.body_center is not None
        ]
        if not recovery_points:
            return StrokeBalanceProxySummary(
                proxy_score=None,
                body_center_drift=None,
                foot_support_range=round(support_range, 4),
                reliable=False,
                reasons=["body center drift could not be measured"],
            )

        average_recovery_center = Point2D(
            x=float(mean(point.x for point in recovery_points)),
            y=float(mean(point.y for point in recovery_points)),
        )
        drift = distance(contact_center, average_recovery_center)
        proxy_score = clamp_unit(1.0 - min(1.0, drift / max(support_range, 0.15)))
        return StrokeBalanceProxySummary(
            proxy_score=round(proxy_score, 4),
            body_center_drift=round(drift, 4),
            foot_support_range=round(support_range, 4),
            reliable=True,
            reasons=[],
        )

    def _classify_confidence(
        self,
        phases: StrokePhasesSummary,
        metrics: StrokeMetricsSummary,
        pose_summary: PoseAnalysisSummary,
        frames: list[StrokeFrameState],
    ) -> StrokeConfidenceLevel:
        if not phases.available or phases.contact_estimate is None:
            return "insufficient"

        visibility_score = float(mean(frame.selected_visibility for frame in frames)) if frames else 0.0
        contact_confidence = phases.contact_estimate.confidence or 0.0
        phase_count = sum(
            1
            for value in (
                phases.ready,
                phases.preparation_start,
                phases.backswing_end,
                phases.contact_estimate,
                phases.follow_through_peak,
                phases.recovery,
            )
            if value is not None
        )
        motion_quality = 1.0 if metrics.wrist_velocity_profile.reliable else 0.0

        computed: StrokeConfidenceLevel
        if contact_confidence >= 0.75 and phase_count >= 5 and visibility_score >= 0.75 and motion_quality > 0.5:
            computed = "high"
        elif contact_confidence >= 0.55 and phase_count >= 4 and visibility_score >= 0.65:
            computed = "medium"
        else:
            computed = "low"

        cap = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}[pose_summary.confidence_level]
        if cap <= 0:
            return "insufficient"
        rank = {"low": 1, "medium": 2, "high": 3}[computed]
        rank = min(rank, cap)
        return {1: "low", 2: "medium", 3: "high"}[rank]

    def _contact_confidence(
        self,
        frame: StrokeFrameState,
        peak_speed: float,
        extension_max: float,
        pose_summary: PoseAnalysisSummary,
    ) -> float:
        speed_score = frame.selected_speed / peak_speed if peak_speed > 0 and frame.selected_speed is not None else 0.0
        extension_score = frame.selected_extension / extension_max if extension_max > 0 and frame.selected_extension is not None else 0.0
        visibility_score = clamp_unit(frame.selected_visibility)
        _ = pose_summary
        return round(clamp_unit((0.45 * speed_score) + (0.30 * extension_score) + (0.25 * visibility_score)), 4)

    def _stroke_score(
        self,
        frame: StrokeFrameState,
        extension_max: float,
        angle_max: float,
        peak_speed: float,
    ) -> float:
        speed_score = clamp_unit((frame.selected_speed or 0.0) / peak_speed) if peak_speed > 0 else 0.0
        extension_score = frame.selected_extension / extension_max if extension_max > 0 and frame.selected_extension is not None else 0.0
        angle_score = frame.selected_elbow_angle / angle_max if angle_max > 0 and frame.selected_elbow_angle is not None else 0.0
        return (0.55 * speed_score) + (0.3 * extension_score) + (0.15 * angle_score)

    def _selected_visibility(self, frame: PoseFrameRecord, dominant_hand: StrokeHandedness) -> float:
        names = [f"{dominant_hand}_shoulder", f"{dominant_hand}_elbow", f"{dominant_hand}_wrist"]
        values: list[float] = []
        for landmark_name in names:
            landmark = next((item for item in frame.landmarks if item.landmark_name == landmark_name), None)
            if landmark is None:
                continue
            values.append(float(landmark.visibility or 0.0))
        return float(mean(values)) if values else 0.0

    def _body_center(self, points: dict[str, Point2D]) -> Point2D | None:
        shoulder_left = points.get("left_shoulder")
        shoulder_right = points.get("right_shoulder")
        hip_left = points.get("left_hip")
        hip_right = points.get("right_hip")
        if shoulder_left and shoulder_right:
            return midpoint(shoulder_left, shoulder_right)
        if hip_left and hip_right:
            return midpoint(hip_left, hip_right)
        if shoulder_left and hip_left:
            return midpoint(shoulder_left, hip_left)
        if shoulder_right and hip_right:
            return midpoint(shoulder_right, hip_right)
        return None

    def _torso_scale(self, points: dict[str, Point2D]) -> float:
        shoulder_left = points.get("left_shoulder")
        shoulder_right = points.get("right_shoulder")
        if shoulder_left and shoulder_right:
            return max(distance(shoulder_left, shoulder_right), 0.001)
        hip_left = points.get("left_hip")
        hip_right = points.get("right_hip")
        if hip_left and hip_right:
            return max(distance(hip_left, hip_right), 0.001)
        shoulder = shoulder_left or shoulder_right
        hip = hip_left or hip_right
        if shoulder and hip:
            return max(distance(shoulder, hip), 0.001)
        return 1.0

    def _support_range(self, points: dict[str, Point2D]) -> float | None:
        for left_name, right_name in (
            ("left_ankle", "right_ankle"),
            ("left_heel", "right_heel"),
            ("left_foot_index", "right_foot_index"),
        ):
            left = points.get(left_name)
            right = points.get(right_name)
            if left and right:
                return distance(left, right)
        return None

    def _event_from_frame(self, frame: StrokeFrameState, evidence: list[str]) -> StrokePhaseEvent:
        return StrokePhaseEvent(
            frame_index=frame.frame_index,
            timestamp_ms=frame.timestamp_ms,
            confidence=round(min(0.95, 0.55 + (frame.selected_visibility * 0.35)), 4),
            evidence=evidence,
        )

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered
