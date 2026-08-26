from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .models import JobArtifacts, JobResponse
from .pose_models import PoseAnalysisSummary
from .stroke_models import StrokeAnalysisSummary
from .technical_feedback import TechnicalFeedbackEngine
from .technical_feedback_models import TechnicalFeedbackSummary


class BackfillAnalysisInput(BaseModel):
    analysis_id: int
    status: str = "completed"
    analysis_mode: str = "stroke"
    source: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class BackfillRequest(BaseModel):
    dry_run: bool = False
    force: bool = False
    analyses: list[BackfillAnalysisInput] = Field(default_factory=list)


class BackfillCapabilitySnapshot(BaseModel):
    stroke_phases: dict[str, Any] = Field(default_factory=dict)
    elbow_angles: dict[str, Any] = Field(default_factory=dict)
    shoulder_rotation: dict[str, Any] = Field(default_factory=dict)
    hip_rotation: dict[str, Any] = Field(default_factory=dict)
    knee_left: dict[str, Any] = Field(default_factory=dict)
    knee_right: dict[str, Any] = Field(default_factory=dict)
    balance: dict[str, Any] = Field(default_factory=dict)
    recovery: dict[str, Any] = Field(default_factory=dict)


def _normalize_feedback(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    technical_feedback = result.get("technical_feedback")
    return technical_feedback if isinstance(technical_feedback, dict) else {}


def _build_job(analysis: BackfillAnalysisInput) -> JobResponse:
    return JobResponse(
        job_id=str(analysis.result.get("job_id") or f"backfill-{analysis.analysis_id}"),
        analysis_id=analysis.analysis_id,
        status=analysis.status,
        workflow_mode="backfill",
        created_at=0.0,
        updated_at=0.0,
        result=None,
        error=None,
        external_artifacts=JobArtifacts(),
        source=analysis.source or {},
    )


def _snapshot_capabilities(stroke: StrokeAnalysisSummary) -> BackfillCapabilitySnapshot:
    capabilities = stroke.analysis_capabilities
    knee_left = capabilities.knee_angles.left
    knee_right = capabilities.knee_angles.right
    return BackfillCapabilitySnapshot(
        stroke_phases=capabilities.stroke_phases.model_dump(mode="json"),
        elbow_angles=capabilities.elbow_angles.model_dump(mode="json"),
        shoulder_rotation=capabilities.shoulder_rotation.model_dump(mode="json"),
        hip_rotation=capabilities.hip_rotation.model_dump(mode="json"),
        knee_left=knee_left.model_dump(mode="json"),
        knee_right=knee_right.model_dump(mode="json"),
        balance=capabilities.balance.model_dump(mode="json"),
        recovery=capabilities.recovery.model_dump(mode="json"),
    )


def _summarize_counts(summary: TechnicalFeedbackSummary) -> dict[str, int]:
    return {
        "findings_count": len(summary.findings),
        "possible_observation_count": len(summary.possible_observations),
        "neutral_measurement_count": len(summary.neutral_measurements),
        "withheld_count": len(summary.withheld_findings),
    }


def _queue_category(summary: TechnicalFeedbackSummary, stroke: StrokeAnalysisSummary, pose: PoseAnalysisSummary) -> str:
    pose_confidence = pose.confidence_level
    stroke_confidence = stroke.confidence_level
    capabilities = stroke.analysis_capabilities
    knee_left = capabilities.knee_angles.left
    knee_right = capabilities.knee_angles.right

    if summary.withheld_findings and not (summary.findings or summary.possible_observations or summary.neutral_measurements):
        return "mostly_withheld"
    if knee_left.available != knee_right.available:
        return "one_knee_only"
    if pose_confidence == "high" and stroke_confidence == "high" and summary.legacy_derived is False:
        return "high_full_body"
    if pose_confidence == "low" or stroke_confidence in {"low", "insufficient"}:
        return "low_confidence"
    if any(
        capability.confidence_level in {"medium", "low"}
        for capability in [
            capabilities.stroke_phases,
            capabilities.elbow_angles,
            capabilities.shoulder_rotation,
            capabilities.hip_rotation,
            capabilities.knee_angles.left,
            capabilities.knee_angles.right,
            capabilities.balance,
            capabilities.recovery,
        ]
    ):
        return "partial_capability"
    return "balanced"


def _build_review_queue_entry(
    analysis: BackfillAnalysisInput,
    summary: TechnicalFeedbackSummary,
    stroke: StrokeAnalysisSummary,
    pose: PoseAnalysisSummary,
    coach_review_exists: bool,
) -> dict[str, Any]:
    counts = _summarize_counts(summary)
    capabilities = stroke.analysis_capabilities
    return {
        "analysis_id": analysis.analysis_id,
        "rules_version": summary.rules_version,
        "available_capabilities": {
            "pose_confidence": pose.confidence_level,
            "stroke_confidence": stroke.confidence_level,
            "stroke_phases": capabilities.stroke_phases.confidence_level,
            "elbow_angles": capabilities.elbow_angles.confidence_level,
            "shoulder_rotation": capabilities.shoulder_rotation.confidence_level,
            "hip_rotation": capabilities.hip_rotation.confidence_level,
            "knee_left": capabilities.knee_angles.left.confidence_level,
            "knee_right": capabilities.knee_angles.right.confidence_level,
            "balance": capabilities.balance.confidence_level,
            "recovery": capabilities.recovery.confidence_level,
            "legacy_derived": summary.legacy_derived,
        },
        "findings_count": counts["findings_count"],
        "possible_observation_count": counts["possible_observation_count"],
        "neutral_measurement_count": counts["neutral_measurement_count"],
        "withheld_count": counts["withheld_count"],
        "video_suitability": "usable"
        if summary.findings or summary.possible_observations
        else ("limited" if pose.confidence_level != "insufficient" and summary.withheld_findings else "unusable"),
        "coach_review_exists": coach_review_exists,
        "queue_category": _queue_category(summary, stroke, pose),
        "generated_at": summary.calculated_at,
    }


def process_backfill_request(request: BackfillRequest) -> dict[str, Any]:
    engine = TechnicalFeedbackEngine()
    current_rules_version = engine._config.version  # noqa: SLF001
    totals = {
        "total_completed": 0,
        "eligible": 0,
        "already_current": 0,
        "legacy_compatible": 0,
        "skipped": 0,
        "updated": 0,
        "errors": 0,
    }
    skip_reasons: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []

    for analysis in request.analyses:
        totals["total_completed"] += 1
        result = analysis.result or {}
        if analysis.status != "completed":
            totals["skipped"] += 1
            reason = "analysis_not_completed"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "skipped",
                    "reason": reason,
                }
            )
            continue

        if analysis.analysis_mode != "stroke":
            totals["skipped"] += 1
            reason = "non_stroke_analysis"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "skipped",
                    "reason": reason,
                }
            )
            continue

        existing_feedback = _normalize_feedback(result)
        existing_rules_version = str(existing_feedback.get("rules_version") or "")
        if existing_feedback and existing_rules_version == current_rules_version and not request.force:
            totals["already_current"] += 1
            skip_reasons["already_current"] = skip_reasons.get("already_current", 0) + 1
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "skipped",
                    "reason": "already_current",
                    "existing_rules_version": existing_rules_version,
                    "rules_version": existing_rules_version,
                }
            )
            continue
        if existing_feedback and existing_rules_version != "" and existing_rules_version != current_rules_version and not request.force:
            totals["skipped"] += 1
            skip_reasons["existing_feedback_version_present"] = skip_reasons.get("existing_feedback_version_present", 0) + 1
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "skipped",
                    "reason": "existing_feedback_version_present",
                    "existing_rules_version": existing_rules_version,
                    "rules_version": existing_rules_version,
                }
            )
            continue

        totals["eligible"] += 1
        try:
            pose = PoseAnalysisSummary.model_validate(result.get("pose") or {})
            stroke = StrokeAnalysisSummary.model_validate(result.get("stroke") or {})
        except ValidationError as exc:
            totals["skipped"] += 1
            totals["errors"] += 1
            reason = "invalid_stored_result"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "error",
                    "reason": reason,
                    "details": exc.errors(),
                }
            )
            continue

        if not stroke.available:
            totals["skipped"] += 1
            reason = "stroke_unavailable"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "skipped",
                    "reason": reason,
                    "stroke_confidence": stroke.confidence_level,
                }
            )
            continue

        effective_stroke = engine._apply_legacy_compatibility(_build_job(analysis), stroke, pose)  # noqa: SLF001
        summary = engine.build(
            _build_job(analysis),
            effective_stroke,
            pose,
            source_analysis_id=analysis.analysis_id,
            generation_mode="backfill",
            calculated_at=datetime.now(UTC).isoformat(),
        )

        if not summary.available:
            totals["skipped"] += 1
            reason = "insufficient_stored_evidence"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            reasons = list(summary.notes)
            if not reasons and summary.withheld_findings:
                reasons = [item.reason for item in summary.withheld_findings if item.reason]
            results.append(
                {
                    "analysis_id": analysis.analysis_id,
                    "status": "skipped",
                    "reason": reason,
                    "details": reasons,
                    "rules_version": summary.rules_version,
                }
            )
            continue

        totals["updated"] += 1
        if summary.legacy_derived:
            totals["legacy_compatible"] += 1

        queue.append(_build_review_queue_entry(analysis, summary, effective_stroke, pose, False))

        results.append(
            {
                "analysis_id": analysis.analysis_id,
                "status": "updated",
                "reason": "",
                "rules_version": summary.rules_version,
                "technical_feedback": summary.model_dump(mode="json", exclude_none=True),
                "pose_confidence": pose.confidence_level,
                "stroke_confidence": effective_stroke.confidence_level,
                "capabilities": _snapshot_capabilities(effective_stroke).model_dump(mode="json", exclude_none=True),
            }
        )

    queue.sort(
        key=lambda item: (
            {
                "high_full_body": 0,
                "partial_capability": 1,
                "low_confidence": 2,
                "one_knee_only": 3,
                "mostly_withheld": 4,
                "balanced": 5,
            }.get(item.get("queue_category", "balanced"), 6),
            -int(item.get("analysis_id", 0)),
        )
    )

    return {
        "summary": {
            "current_rules_version": current_rules_version,
            "dry_run": request.dry_run,
            "force": request.force,
            "totals": totals,
            "skip_reasons": skip_reasons,
        },
        "results": results,
        "queue": queue[:20],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill technical feedback from stored analysis results.")
    parser.add_argument("--input", default="-", help="Input JSON path or - for stdin.")
    parser.add_argument("--output", default="-", help="Output JSON path or - for stdout.")
    return parser.parse_args(argv)


def _read_payload(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise ValueError("Request payload must be a JSON object.")
    return payload


def _write_payload(path: str, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if path == "-":
        sys.stdout.write(encoded)
        sys.stdout.write("\n")
    else:
        Path(path).write_text(encoded, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        request = BackfillRequest.model_validate(_read_payload(args.input))
        response = process_backfill_request(request)
        _write_payload(args.output, response)
    except Exception as exc:  # noqa: BLE001
        error_payload = {"error": {"type": exc.__class__.__name__, "message": str(exc)}}
        if args.output == "-":
            sys.stdout.write(json.dumps(error_payload, ensure_ascii=False))
            sys.stdout.write("\n")
        else:
            Path(args.output).write_text(json.dumps(error_payload, ensure_ascii=False), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
