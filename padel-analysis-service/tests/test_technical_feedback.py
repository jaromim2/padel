from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import JobArtifacts, JobResponse, JobResult
from app.pose_models import PoseAnalysisSummary
from app.stroke_models import (
    StrokeAnalysisCapabilitySummary,
    StrokeAnalysisSummary,
    StrokeAngleMeasurement,
    StrokeBalanceProxySummary,
    StrokeCapabilityAssessment,
    StrokeContactFrame,
    StrokeKneeAnglesAtContact,
    StrokeKneeCapabilitySummary,
    StrokeMetricsSummary,
    StrokePhaseEvent,
    StrokePhaseMetricTriple,
    StrokePhasesSummary,
    StrokeSmoothingSummary,
    StrokeWristVelocityProfile,
)
from app.technical_feedback import TechnicalFeedbackEngine
from app.technical_feedback_models import TechnicalFeedbackSummary
from app.technical_feedback_models import TechnicalFeedbackFinding


def _job() -> JobResponse:
    return JobResponse(
        job_id="job-1",
        analysis_id=166,
        status="completed",
        workflow_mode="worker",
        created_at=0.0,
        updated_at=0.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(),
        source={"shot_type": "forehand", "dominant_hand": "right", "camera_angle": "side"},
    )


def _pose(confidence: str = "high", coverage: float = 1.0) -> PoseAnalysisSummary:
    return PoseAnalysisSummary(
        enabled=True,
        confidence_level=confidence,  # type: ignore[arg-type]
        pose_coverage=coverage,
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


def _angle(frame_index: int, timestamp_ms: int, value: float, raw: float | None = None) -> StrokeAngleMeasurement:
    return StrokeAngleMeasurement(
        available=True,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        value_degrees=value,
        raw_value_degrees=raw if raw is not None else value,
        evidence=["synthetic"],
    )


def _capability(confidence: str = "high", available: bool = True) -> StrokeCapabilityAssessment:
    return StrokeCapabilityAssessment(
        available=available,
        confidence_level=confidence,  # type: ignore[arg-type]
        required_landmarks=["synthetic"],
        diagnostics=[],
    )


def _stroke_summary(
    *,
    pose_confidence: str = "high",
    shoulder_delta: float = 10.0,
    hip_delta: float = 8.0,
    knee_left: float | None = 166.0,
    knee_right: float | None = 168.0,
    elbow_contact: float | None = 146.0,
    balance_score: float | None = 0.50,
    recovery_ms: int | None = 260,
    phase_confidence: str = "high",
    shoulder_capability: StrokeCapabilityAssessment | None = None,
    hip_capability: StrokeCapabilityAssessment | None = None,
    elbow_capability: StrokeCapabilityAssessment | None = None,
    balance_capability: StrokeCapabilityAssessment | None = None,
    recovery_capability: StrokeCapabilityAssessment | None = None,
    knee_left_capability: StrokeCapabilityAssessment | None = None,
    knee_right_capability: StrokeCapabilityAssessment | None = None,
) -> tuple[StrokeAnalysisSummary, PoseAnalysisSummary]:
    prep = _phase(60, 2000)
    contact = _contact(120, 4000)
    follow = _phase(130, 4330)
    recovery = _phase(136, 4520)

    stroke = StrokeAnalysisSummary(
        enabled=True,
        available=True,
        dominant_hand="right",
        confidence_level=phase_confidence,  # type: ignore[arg-type]
        smoothing=StrokeSmoothingSummary(enabled=True, method="ema", alpha=0.35, notes=[]),
        analysis_capabilities=StrokeAnalysisCapabilitySummary(
            stroke_phases=_capability(phase_confidence),
            elbow_angles=elbow_capability or _capability(),
            shoulder_rotation=shoulder_capability or _capability(),
            hip_rotation=hip_capability or _capability(),
            knee_angles=StrokeKneeCapabilitySummary(
                left=knee_left_capability or _capability(),
                right=knee_right_capability or _capability(),
            ),
            balance=balance_capability or _capability(),
            recovery=recovery_capability or _capability(),
        ),
        phases=StrokePhasesSummary(
            available=True,
            confidence_level=phase_confidence,  # type: ignore[arg-type]
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
                contact=_angle(contact.frame_index, contact.timestamp_ms, elbow_contact if elbow_contact is not None else 146.0),
                follow_through=_angle(follow.frame_index, follow.timestamp_ms, 170.0),
            ),
            shoulder_line_orientation=StrokePhaseMetricTriple(
                preparation=_angle(prep.frame_index, prep.timestamp_ms, 10.0),
                contact=_angle(contact.frame_index, contact.timestamp_ms, 10.0 + shoulder_delta),
                follow_through=_angle(follow.frame_index, follow.timestamp_ms, 10.0 + shoulder_delta + 6.0),
            ),
            hip_line_orientation=StrokePhaseMetricTriple(
                preparation=_angle(prep.frame_index, prep.timestamp_ms, 5.0),
                contact=_angle(contact.frame_index, contact.timestamp_ms, 5.0 + hip_delta),
                follow_through=_angle(follow.frame_index, follow.timestamp_ms, 5.0 + hip_delta + 4.0),
            ),
            knee_angles_at_contact=StrokeKneeAnglesAtContact(
                left=_angle(contact.frame_index, contact.timestamp_ms, knee_left) if knee_left is not None else None,
                right=_angle(contact.frame_index, contact.timestamp_ms, knee_right) if knee_right is not None else None,
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
            recovery_duration_ms=recovery_ms,
            balance_proxy=StrokeBalanceProxySummary(
                proxy_score=balance_score,
                body_center_drift=0.32,
                foot_support_range=0.61,
                reliable=balance_score is not None,
                reasons=[] if balance_score is not None else ["unreliable"],
            ),
        ),
        reasons=[],
    )
    if shoulder_capability is not None:
        stroke.analysis_capabilities.shoulder_rotation = shoulder_capability
    if hip_capability is not None:
        stroke.analysis_capabilities.hip_rotation = hip_capability
    if elbow_capability is not None:
        stroke.analysis_capabilities.elbow_angles = elbow_capability
    if balance_capability is not None:
        stroke.analysis_capabilities.balance = balance_capability
    if recovery_capability is not None:
        stroke.analysis_capabilities.recovery = recovery_capability
    if knee_left_capability is not None:
        stroke.analysis_capabilities.knee_angles.left = knee_left_capability
    if knee_right_capability is not None:
        stroke.analysis_capabilities.knee_angles.right = knee_right_capability

    return stroke, _pose(pose_confidence)


def _findings_by_rule(summary: TechnicalFeedbackSummary) -> dict[str, object]:
    return {item.rule_id: item for item in summary.findings}


def test_feedback_emits_measured_findings_and_limits_total() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary(
        shoulder_delta=8.0,
        hip_delta=6.0,
        knee_left=150.0,
        knee_right=150.0,
        elbow_contact=145.0,
        balance_score=0.82,
        recovery_ms=100,
    )

    summary = engine.build(_job(), stroke, pose)

    assert summary.available is True
    assert summary.rules_version == "technical-feedback-v1"
    assert len(summary.findings) <= 5
    assert len({finding.rule_id for finding in summary.findings}) == len(summary.findings)

    rule_ids = {finding.rule_id for finding in summary.findings}
    assert "late_preparation" in rule_ids
    assert "limited_shoulder_rotation" in rule_ids
    assert "arm_too_folded_at_contact" in rule_ids
    assert all(finding.evidence for finding in summary.findings)
    assert all(finding.primary_frame_index is not None for finding in summary.findings)


def test_feedback_withholds_arm_and_balance_when_capabilities_missing() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary(
        elbow_capability=_capability(available=False, confidence="insufficient"),
        balance_capability=_capability(available=False, confidence="insufficient"),
        recovery_capability=_capability(available=False, confidence="insufficient"),
        balance_score=None,
        recovery_ms=None,
    )

    summary = engine.build(_job(), stroke, pose)
    rule_ids = {finding.rule_id for finding in summary.findings}
    withheld_ids = {item.rule_id for item in summary.withheld_findings}

    assert "arm_too_folded_at_contact" not in rule_ids
    assert "arm_too_folded_at_contact" in withheld_ids
    assert "unstable_finish" in withheld_ids or "slow_recovery" in withheld_ids
    assert any(item.reason for item in summary.withheld_findings)


def test_low_confidence_result_is_marked_as_possible_observation() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary(
        shoulder_delta=23.0,
        hip_delta=17.0,
        elbow_contact=151.0,
        pose_confidence="low",
        phase_confidence="low",
    )
    stroke.analysis_capabilities.shoulder_rotation = _capability("medium")
    stroke.analysis_capabilities.hip_rotation = _capability("medium")
    stroke.analysis_capabilities.elbow_angles = _capability("medium")

    summary = engine.build(_job(), stroke, pose)
    possible = _findings_by_rule(type("obj", (), {"findings": summary.possible_observations})())

    assert "limited_shoulder_rotation" not in {finding.rule_id for finding in summary.findings}
    assert possible["limited_shoulder_rotation"].classification == "possible_observation"
    assert possible["limited_shoulder_rotation"].confidence_level == "low"
    assert "המדידה מסומנת כצפייה אפשרית בלבד." in possible["limited_shoulder_rotation"].limitations


def test_low_confidence_finding_is_withheld_when_capability_is_below_minimum() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary(
        shoulder_delta=57.9,
        hip_delta=94.6,
        phase_confidence="low",
        pose_confidence="low",
    )
    stroke.analysis_capabilities.stroke_phases = _capability("low")
    stroke.analysis_capabilities.shoulder_rotation = _capability("high")
    stroke.analysis_capabilities.hip_rotation = _capability("high")

    summary = engine.build(_job(), stroke, pose)
    withheld_ids = {item.rule_id for item in summary.withheld_findings}

    assert "late_preparation" in withheld_ids


def test_job_result_serializes_technical_feedback() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary()
    summary = engine.build(_job(), stroke, pose)

    result = JobResult(
        summary="Video analysis completed successfully.",
        findings=[],
        recommendations=[],
        analysis_id=166,
        job_id="job-1",
        processor="opencv_heuristic_video_analyzer",
        service_version="0.1.0",
        pose=pose,
        stroke=stroke,
        technical_feedback=summary,
    )
    payload = result.model_dump(mode="json")
    restored = JobResult.model_validate(payload)

    assert restored.technical_feedback is not None
    assert restored.technical_feedback.rules_version == "technical-feedback-v1"


def test_feedback_normalizes_angle_wraparound_across_180_degrees() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary(shoulder_delta=0.0, hip_delta=0.0)
    stroke.metrics.shoulder_line_orientation.preparation.value_degrees = 179.0
    stroke.metrics.shoulder_line_orientation.contact.value_degrees = -170.0
    stroke.analysis_capabilities.shoulder_rotation = _capability("high")

    summary = engine.build(_job(), stroke, pose)
    findings = _findings_by_rule(summary)

    assert "limited_shoulder_rotation" in findings
    assert findings["limited_shoulder_rotation"].measured_value == pytest.approx(11.0)
    assert findings["limited_shoulder_rotation"].confidence_level == "high"


def test_feedback_dedupes_duplicate_findings_from_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "duplicate-rules.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "duplicate-rules-v1",
                "rules": [
                    {
                        "id": "limited_shoulder_rotation",
                        "family": "shoulder_rotation",
                        "metric": "shoulder_rotation_prep_to_contact",
                        "comparison": "absolute_less_than",
                        "provisional_threshold": 24.0,
                        "minimum_capability_confidence": "medium",
                        "explanation": "duplicate rule",
                        "status": "experimental",
                        "version": "duplicate-rules-v1",
                        "unit": "degrees",
                        "notes": [],
                        "required_capabilities": ["shoulder_rotation"],
                    },
                    {
                        "id": "limited_shoulder_rotation",
                        "family": "shoulder_rotation",
                        "metric": "shoulder_rotation_prep_to_contact",
                        "comparison": "absolute_less_than",
                        "provisional_threshold": 24.0,
                        "minimum_capability_confidence": "medium",
                        "explanation": "duplicate rule",
                        "status": "experimental",
                        "version": "duplicate-rules-v1",
                        "unit": "degrees",
                        "notes": [],
                        "required_capabilities": ["shoulder_rotation"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    engine = TechnicalFeedbackEngine(config_path=config_path)
    stroke, pose = _stroke_summary(shoulder_delta=8.0, hip_delta=30.0)
    summary = engine.build(_job(), stroke, pose)

    assert summary.rules_version == "duplicate-rules-v1"
    assert [finding.rule_id for finding in summary.findings] == ["limited_shoulder_rotation"]


def test_feedback_limits_primary_and_secondary_counts() -> None:
    engine = TechnicalFeedbackEngine()
    findings = [
        TechnicalFeedbackFinding(
            rule_id="primary-1",
            family="test",
            title="primary 1",
            classification="confirmed_observation",
            confidence_level="high",
            importance="primary",
            observation="o",
            metric_name="m",
            measured_value=1.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="primary-2",
            family="test",
            title="primary 2",
            classification="confirmed_observation",
            confidence_level="medium",
            importance="primary",
            observation="o",
            metric_name="m",
            measured_value=2.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="primary-3",
            family="test",
            title="primary 3",
            classification="confirmed_observation",
            confidence_level="low",
            importance="primary",
            observation="o",
            metric_name="m",
            measured_value=3.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="primary-4",
            family="test",
            title="primary 4",
            classification="confirmed_observation",
            confidence_level="low",
            importance="primary",
            observation="o",
            metric_name="m",
            measured_value=4.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="secondary-1",
            family="test",
            title="secondary 1",
            classification="possible_observation",
            confidence_level="low",
            importance="secondary",
            observation="o",
            metric_name="m",
            measured_value=5.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="secondary-2",
            family="test",
            title="secondary 2",
            classification="possible_observation",
            confidence_level="low",
            importance="secondary",
            observation="o",
            metric_name="m",
            measured_value=6.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="secondary-3",
            family="test",
            title="secondary 3",
            classification="possible_observation",
            confidence_level="low",
            importance="secondary",
            observation="o",
            metric_name="m",
            measured_value=7.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
        TechnicalFeedbackFinding(
            rule_id="secondary-4",
            family="test",
            title="secondary 4",
            classification="possible_observation",
            confidence_level="insufficient",
            importance="secondary",
            observation="o",
            metric_name="m",
            measured_value=8.0,
            threshold_value=0.0,
            unit="u",
            comparison="greater_than",
        ),
    ]

    limited = engine._limit_findings(findings)
    primary_count = sum(1 for finding in limited if finding.importance == "primary")
    secondary_count = sum(1 for finding in limited if finding.importance == "secondary")

    assert len(limited) == 5
    assert primary_count == 3
    assert secondary_count == 2


def test_feedback_loads_external_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "custom-rules.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "custom-feedback-v2",
                "rules": [
                    {
                        "id": "limited_shoulder_rotation",
                        "family": "shoulder_rotation",
                        "metric": "shoulder_rotation_prep_to_contact",
                        "comparison": "absolute_less_than",
                        "provisional_threshold": 1000.0,
                        "minimum_capability_confidence": "medium",
                        "explanation": "custom rule",
                        "status": "experimental",
                        "version": "custom-feedback-v2",
                        "unit": "degrees",
                        "notes": [],
                        "required_capabilities": ["shoulder_rotation"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    engine = TechnicalFeedbackEngine(config_path=config_path)
    stroke, pose = _stroke_summary(shoulder_delta=8.0, hip_delta=30.0)
    stroke.analysis_capabilities.shoulder_rotation = _capability("high")

    summary = engine.build(_job(), stroke, pose)

    assert summary.rules_version == "custom-feedback-v2"
    assert [finding.rule_id for finding in summary.findings] == ["limited_shoulder_rotation"]


def test_feedback_rejects_missing_or_invalid_rules_configuration(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        TechnicalFeedbackEngine(config_path=tmp_path / "missing.json")

    invalid_config = tmp_path / "invalid.json"
    invalid_config.write_text(json.dumps({"version": "broken", "rules": [{}]}), encoding="utf-8")

    with pytest.raises(ValidationError):
        TechnicalFeedbackEngine(config_path=invalid_config)


def test_feedback_matches_analysis_166_style_partial_result() -> None:
    engine = TechnicalFeedbackEngine()
    stroke, pose = _stroke_summary(
        shoulder_delta=57.9,
        hip_delta=94.6,
        knee_left=None,
        knee_right=177.1,
        elbow_contact=165.6,
        balance_score=None,
        recovery_ms=None,
        phase_confidence="low",
        pose_confidence="low",
        knee_left_capability=_capability(available=False, confidence="insufficient"),
        knee_right_capability=_capability("medium"),
        elbow_capability=_capability(available=False, confidence="insufficient"),
        balance_capability=_capability(available=False, confidence="insufficient"),
        recovery_capability=_capability(available=False, confidence="insufficient"),
    )
    stroke.analysis_capabilities.shoulder_rotation = _capability("high")
    stroke.analysis_capabilities.hip_rotation = _capability("high")
    stroke.metrics.shoulder_line_orientation.follow_through.value_degrees = 144.0

    summary = engine.build(_job(), stroke, pose)
    rule_ids = {finding.rule_id for finding in summary.findings}
    possible_ids = {finding.rule_id for finding in summary.possible_observations}
    neutral_ids = {finding.rule_id for finding in summary.neutral_measurements}
    withheld_ids = {item.rule_id for item in summary.withheld_findings}

    assert rule_ids == set()
    assert possible_ids == {"limited_knee_bend_right"}
    assert neutral_ids == {"shoulder_hip_sequence_difference"}
    assert {
        "late_preparation",
        "limited_knee_bend_left",
        "arm_too_folded_at_contact",
        "arm_near_full_extension_at_contact",
        "unstable_finish",
        "slow_recovery",
    }.issubset(withheld_ids)
