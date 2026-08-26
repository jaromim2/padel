from __future__ import annotations

import copy

from app.backfill_feedback import BackfillAnalysisInput, BackfillRequest, process_backfill_request
from app.pose_models import PoseAnalysisSummary
from app.stroke_models import (
    StrokeAnalysisSummary,
    StrokeAngleMeasurement,
    StrokeBalanceProxySummary,
    StrokeContactFrame,
    StrokeMetricsSummary,
    StrokePhaseEvent,
    StrokePhaseMetricTriple,
    StrokePhasesSummary,
    StrokeSmoothingSummary,
    StrokeKneeAnglesAtContact,
    StrokeWristVelocityProfile,
)


def _pose(confidence: str = "high") -> PoseAnalysisSummary:
    return PoseAnalysisSummary(
        enabled=True,
        confidence_level=confidence,  # type: ignore[arg-type]
        pose_coverage=1.0,
        full_body_visible=True,
        total_frames=200,
        processed_frames=200,
        frames_with_pose=200,
    )


def _phase(frame_index: int, timestamp_ms: int, confidence: float = 0.9) -> StrokePhaseEvent:
    return StrokePhaseEvent(frame_index=frame_index, timestamp_ms=timestamp_ms, confidence=confidence)


def _contact(frame_index: int, timestamp_ms: int, confidence: float = 0.9) -> StrokeContactFrame:
    return StrokeContactFrame(
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        confidence=confidence,
        source="estimated",
        candidate_start_frame=max(0, frame_index - 3),
        candidate_end_frame=frame_index + 3,
    )


def _angle(frame_index: int, timestamp_ms: int, value: float) -> StrokeAngleMeasurement:
    return StrokeAngleMeasurement(
        available=True,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        value_degrees=value,
        raw_value_degrees=value,
        evidence=["synthetic"],
    )


def _stroke(*, available: bool = True) -> StrokeAnalysisSummary:
    prep = _phase(60, 2000)
    contact = _contact(120, 4000)
    follow = _phase(130, 4330)
    recovery = _phase(136, 4520)
    return StrokeAnalysisSummary(
        enabled=True,
        available=available,
        dominant_hand="right",
        confidence_level="high",  # type: ignore[arg-type]
        smoothing=StrokeSmoothingSummary(enabled=True, method="ema", alpha=0.35, notes=[]),
        phases=StrokePhasesSummary(
            available=available,
            confidence_level="high",  # type: ignore[arg-type]
            ready=_phase(0, 0),
            preparation_start=prep,
            backswing_end=_phase(65, 2167),
            contact_estimate=contact,
            original_contact_estimate=contact,
            follow_through_peak=follow,
            recovery=recovery,
            reasons=[],
        ),
        metrics=StrokeMetricsSummary(
            dominant_elbow_angle=StrokePhaseMetricTriple(
                preparation=_angle(prep.frame_index, prep.timestamp_ms, 152.0),
                contact=_angle(contact.frame_index, contact.timestamp_ms, 146.0),
                follow_through=_angle(follow.frame_index, follow.timestamp_ms, 170.0),
            ),
            shoulder_line_orientation=StrokePhaseMetricTriple(
                preparation=_angle(prep.frame_index, prep.timestamp_ms, 10.0),
                contact=_angle(contact.frame_index, contact.timestamp_ms, 18.0),
                follow_through=_angle(follow.frame_index, follow.timestamp_ms, 24.0),
            ),
            hip_line_orientation=StrokePhaseMetricTriple(
                preparation=_angle(prep.frame_index, prep.timestamp_ms, 5.0),
                contact=_angle(contact.frame_index, contact.timestamp_ms, 13.0),
                follow_through=_angle(follow.frame_index, follow.timestamp_ms, 17.0),
            ),
            knee_angles_at_contact=StrokeKneeAnglesAtContact(
                left=_angle(contact.frame_index, contact.timestamp_ms, 149.0),
                right=_angle(contact.frame_index, contact.timestamp_ms, 151.0),
            ),
            wrist_velocity_profile=StrokeWristVelocityProfile(
                peak_normalized_velocity=0.9,
                peak_frame_index=125,
                peak_timestamp_ms=4167,
                velocity_at_contact=0.5,
                peak_acceleration=0.8,
                peak_acceleration_frame_index=126,
                reliable=True,
                evidence=["synthetic"],
            ),
            recovery_duration_ms=260,
            balance_proxy=StrokeBalanceProxySummary(
                proxy_score=0.8,
                body_center_drift=0.32,
                foot_support_range=0.61,
                reliable=True,
                reasons=[],
            ),
        ),
        reasons=[],
    )


def _analysis_input(
    *,
    analysis_id: int,
    pose_confidence: str = "high",
    stroke_available: bool = True,
    existing_feedback_version: str = "",
) -> BackfillAnalysisInput:
    result = {
        "pose": _pose(pose_confidence).model_dump(mode="json"),
        "stroke": _stroke(available=stroke_available).model_dump(mode="json"),
        "job_id": f"job-{analysis_id}",
    }
    if existing_feedback_version:
        result["technical_feedback"] = {
            "rules_version": existing_feedback_version,
            "findings": [{"rule_id": "existing_rule"}],
        }
    return BackfillAnalysisInput(
        analysis_id=analysis_id,
        status="completed",
        analysis_mode="stroke",
        source={"camera_angle": "side", "dominant_hand": "right", "shot_type": "forehand"},
        result=result,
    )


def test_dry_run_preserves_input_payload() -> None:
    request = BackfillRequest(dry_run=True, analyses=[_analysis_input(analysis_id=1)])
    original = copy.deepcopy(request.model_dump(mode="json"))
    response = process_backfill_request(request)
    assert request.model_dump(mode="json") == original
    assert response["summary"]["dry_run"] is True
    assert response["results"][0]["status"] == "updated"


def test_current_rules_version_is_skipped() -> None:
    request = BackfillRequest(
        analyses=[_analysis_input(analysis_id=2, existing_feedback_version="technical-feedback-v1")]
    )
    response = process_backfill_request(request)
    assert response["results"][0]["status"] == "skipped"
    assert response["results"][0]["reason"] == "already_current"
    assert response["summary"]["totals"]["already_current"] == 1


def test_force_overwrites_current_version() -> None:
    request = BackfillRequest(
        force=True,
        analyses=[_analysis_input(analysis_id=3, existing_feedback_version="technical-feedback-v1")],
    )
    response = process_backfill_request(request)
    assert response["results"][0]["status"] == "updated"
    assert response["results"][0]["technical_feedback"]["rules_version"] == "technical-feedback-v1"


def test_legacy_derived_evidence_is_marked() -> None:
    request = BackfillRequest(analyses=[_analysis_input(analysis_id=4, pose_confidence="medium")])
    response = process_backfill_request(request)
    assert response["results"][0]["status"] == "updated"
    assert response["results"][0]["technical_feedback"]["legacy_derived"] is True
    assert response["summary"]["totals"]["legacy_compatible"] == 1


def test_insufficient_legacy_analysis_is_skipped() -> None:
    request = BackfillRequest(analyses=[_analysis_input(analysis_id=5, stroke_available=False)])
    response = process_backfill_request(request)
    assert response["results"][0]["status"] == "skipped"
    assert response["results"][0]["reason"] == "stroke_unavailable"


def test_repeat_backfill_is_idempotent() -> None:
    initial = BackfillRequest(analyses=[_analysis_input(analysis_id=6)])
    first = process_backfill_request(initial)
    updated_feedback = first["results"][0]["technical_feedback"]
    second_request = BackfillRequest(
        analyses=[
            BackfillAnalysisInput(
                analysis_id=6,
                status="completed",
                analysis_mode="stroke",
                source={"camera_angle": "side", "dominant_hand": "right", "shot_type": "forehand"},
                result={
                    "pose": _pose().model_dump(mode="json"),
                    "stroke": _stroke().model_dump(mode="json"),
                    "technical_feedback": updated_feedback,
                },
            )
        ]
    )
    second = process_backfill_request(second_request)
    assert second["results"][0]["status"] == "skipped"
    assert second["results"][0]["reason"] == "already_current"


def test_calibration_queue_counts_match_backfilled_result() -> None:
    request = BackfillRequest(analyses=[_analysis_input(analysis_id=7)])
    response = process_backfill_request(request)
    queue_entry = response["queue"][0]
    feedback = response["results"][0]["technical_feedback"]
    assert queue_entry["analysis_id"] == 7
    assert queue_entry["rules_version"] == feedback["rules_version"]
    assert queue_entry["findings_count"] == len(feedback["findings"])
    assert queue_entry["possible_observation_count"] == len(feedback["possible_observations"])
    assert queue_entry["neutral_measurement_count"] == len(feedback["neutral_measurements"])
    assert queue_entry["withheld_count"] == len(feedback["withheld_findings"])
