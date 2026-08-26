from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from .models import JobResponse
from .pose_models import PoseAnalysisSummary
from .stroke_geometry import shortest_angle_delta
from .stroke_models import (
    StrokeAngleMeasurement,
    StrokeAnalysisCapabilitySummary,
    StrokeAnalysisSummary,
    StrokeCapabilityAssessment,
    StrokeConfidenceLevel,
    StrokePhaseEvent,
    StrokePhasesSummary,
)
from .technical_feedback_models import (
    TechnicalFeedbackClassification,
    TechnicalFeedbackConfidenceLevel,
    TechnicalFeedbackEvidence,
    TechnicalFeedbackFinding,
    TechnicalFeedbackImportance,
    TechnicalFeedbackPhaseReference,
    TechnicalFeedbackRuleConfig,
    TechnicalFeedbackRuleSet,
    TechnicalFeedbackSummary,
    TechnicalFeedbackWithheldFinding,
)


@dataclass(slots=True)
class TechnicalFeedbackDecision:
    findings: list[TechnicalFeedbackFinding] = field(default_factory=list)
    possible_observations: list[TechnicalFeedbackFinding] = field(default_factory=list)
    neutral_measurements: list[TechnicalFeedbackFinding] = field(default_factory=list)
    withheld: list[TechnicalFeedbackWithheldFinding] = field(default_factory=list)


class TechnicalFeedbackEngine:
    def __init__(self, config_path: Path | None = None) -> None:
        self._config = self._load_config(config_path)

    def build(
        self,
        job: JobResponse,
        stroke: StrokeAnalysisSummary | None,
        pose: PoseAnalysisSummary | None,
        *,
        source_analysis_id: int | None = None,
        generation_mode: str = "normal",
        calculated_at: str | None = None,
    ) -> TechnicalFeedbackSummary:
        self._current_camera_angle = self._canonical_camera_angle(job.source.get("camera_angle") if job.source else None)
        if stroke is None or not stroke.available:
            return TechnicalFeedbackSummary(
                available=False,
                rules_version=self._config.version,
                source_analysis_id=source_analysis_id,
                calculated_at=calculated_at or datetime.now(UTC).isoformat(),
                generation_mode="backfill" if generation_mode == "backfill" else "normal",
                findings=[],
                withheld_findings=[
                    TechnicalFeedbackWithheldFinding(
                        rule_id="stroke_analysis_unavailable",
                        family="availability",
                        title="נדרשת תוצאת stroke פעילה",
                        reason="אין מספיק מידע כדי להעריך ממצאי טכניקה.",
                        missing_capabilities=["stroke_phases"],
                        limitations=["הניתוח הטכני מבוסס על stroke פעיל בלבד."],
                        supporting_details={
                            "stroke_available": bool(stroke.available) if stroke is not None else False,
                            "pose_confidence": pose.confidence_level if pose is not None else "insufficient",
                        },
                    )
                ],
                notes=["technical feedback withheld because stroke analysis is unavailable"],
            )

        stroke = self._apply_legacy_compatibility(job, stroke, pose)

        decisions: list[TechnicalFeedbackDecision] = []
        for rule in self._config.rules:
            decisions.append(self._evaluate_rule(rule, stroke, pose))

        findings = self._dedupe_findings([finding for decision in decisions for finding in decision.findings])
        possible_observations = self._dedupe_findings(
            [finding for decision in decisions for finding in decision.possible_observations]
        )
        neutral_measurements = self._dedupe_findings(
            [finding for decision in decisions for finding in decision.neutral_measurements]
        )
        withheld = [item for decision in decisions for item in decision.withheld]
        findings = self._limit_findings(findings)

        return TechnicalFeedbackSummary(
            available=bool(findings or possible_observations or neutral_measurements or withheld),
            rules_version=self._config.version,
            source_analysis_id=source_analysis_id,
            calculated_at=calculated_at or datetime.now(UTC).isoformat(),
            generation_mode="backfill" if generation_mode == "backfill" else "normal",
            legacy_derived=self._is_legacy_derived(stroke),
            findings=findings,
            possible_observations=possible_observations,
            neutral_measurements=neutral_measurements,
            withheld_findings=withheld,
            notes=[],
        )

    def _load_config(self, config_path: Path | None) -> TechnicalFeedbackRuleSet:
        path = config_path or Path(__file__).resolve().parents[1] / "config" / "technical-feedback-rules.json"
        return TechnicalFeedbackRuleSet.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _apply_legacy_compatibility(
        self,
        job: JobResponse,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
    ) -> StrokeAnalysisSummary:
        capabilities = stroke.analysis_capabilities
        if any(self._confidence_rank(cap.confidence_level) >= self._confidence_rank("low") for cap in self._iter_capabilities(capabilities)):
            return stroke

        derived = self._derive_legacy_capabilities(job, stroke, pose)
        if derived is None:
            return stroke

        return stroke.model_copy(update={"analysis_capabilities": derived})

    def _derive_legacy_capabilities(
        self,
        job: JobResponse,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
    ) -> StrokeAnalysisCapabilitySummary | None:
        metrics = stroke.metrics
        phases = stroke.phases
        camera_angle = self._canonical_camera_angle(job.source.get("camera_angle") if job.source else None)

        if metrics is None or phases is None:
            return None

        capabilities = StrokeAnalysisCapabilitySummary()
        derived_any = False

        if phases.available and phases.ready and phases.preparation_start and phases.contact_estimate:
            capabilities.stroke_phases = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("phase", phases.confidence_level, pose, camera_angle),
                required_landmarks=["stroke_phases"],
                reasons=["legacy derived from stored stroke phases"],
            )
            derived_any = True

        if metrics.shoulder_line_orientation.preparation and metrics.shoulder_line_orientation.contact:
            capabilities.shoulder_rotation = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("rotation", stroke.phases.confidence_level, pose, camera_angle),
                required_landmarks=["shoulder_rotation"],
                reasons=["legacy derived from stored shoulder rotation"],
            )
            derived_any = True

        if metrics.hip_line_orientation.preparation and metrics.hip_line_orientation.contact:
            capabilities.hip_rotation = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("rotation", stroke.phases.confidence_level, pose, camera_angle),
                required_landmarks=["hip_rotation"],
                reasons=["legacy derived from stored hip rotation"],
            )
            derived_any = True

        if metrics.dominant_elbow_angle.contact is not None:
            capabilities.elbow_angles = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("elbow", stroke.phases.confidence_level, pose, camera_angle),
                required_landmarks=["elbow_angles"],
                reasons=["legacy derived from stored elbow angle"],
            )
            derived_any = True

        if metrics.knee_angles_at_contact.left is not None:
            capabilities.knee_angles.left = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("knee", stroke.phases.confidence_level, pose, camera_angle),
                required_landmarks=["knee_angles.left"],
                reasons=["legacy derived from stored left knee angle"],
            )
            derived_any = True

        if metrics.knee_angles_at_contact.right is not None:
            capabilities.knee_angles.right = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("knee", stroke.phases.confidence_level, pose, camera_angle),
                required_landmarks=["knee_angles.right"],
                reasons=["legacy derived from stored right knee angle"],
            )
            derived_any = True

        if metrics.balance_proxy.reliable and metrics.balance_proxy.proxy_score is not None:
            capabilities.balance = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("balance", stroke.phases.confidence_level, pose, camera_angle),
                required_landmarks=["balance_proxy"],
                reasons=["legacy derived from stored balance proxy"],
            )
            derived_any = True

        if metrics.recovery_duration_ms is not None and phases.recovery is not None and phases.follow_through_peak is not None:
            capabilities.recovery = self._legacy_capability(
                available=True,
                confidence_level=self._legacy_capability_confidence("recovery", phases.confidence_level, pose, camera_angle),
                required_landmarks=["recovery_timing"],
                reasons=["legacy derived from stored recovery timing"],
            )
            derived_any = True

        return capabilities if derived_any else None

    def _legacy_capability(
        self,
        *,
        available: bool,
        confidence_level: TechnicalFeedbackConfidenceLevel,
        required_landmarks: list[str],
        reasons: list[str],
    ) -> StrokeCapabilityAssessment:
        return StrokeCapabilityAssessment(
            available=available,
            confidence_level=confidence_level,
            confidence_source="legacy_derived",
            required_landmarks=required_landmarks,
            reasons=reasons,
        )

    def _legacy_capability_confidence(
        self,
        capability_type: str,
        phase_confidence: StrokeConfidenceLevel,
        pose: PoseAnalysisSummary | None,
        camera_angle: str | None,
    ) -> TechnicalFeedbackConfidenceLevel:
        ceiling = "medium"
        if capability_type in {"phase", "rotation", "knee"}:
            ceiling = "medium"
        elif capability_type in {"elbow", "balance", "recovery"}:
            ceiling = "low"

        dependency = self._lowest_confidence(
            self._lowest_confidence(self._angle_camera_confidence(camera_angle), phase_confidence),
            pose.confidence_level if pose is not None else "insufficient",
        )
        return self._lowest_confidence(ceiling, dependency)

    def _iter_capabilities(self, capabilities: StrokeAnalysisCapabilitySummary):
        yield capabilities.stroke_phases
        yield capabilities.elbow_angles
        yield capabilities.shoulder_rotation
        yield capabilities.hip_rotation
        yield capabilities.knee_angles.left
        yield capabilities.knee_angles.right
        yield capabilities.balance
        yield capabilities.recovery

    def _is_legacy_derived(self, stroke: StrokeAnalysisSummary) -> bool:
        return any(
            capability.confidence_source == "legacy_derived"
            for capability in self._iter_capabilities(stroke.analysis_capabilities)
        )

    def _canonical_camera_angle(self, raw_value: object | None) -> str:
        value = str(raw_value or "").strip().lower()
        if not value:
            return "unknown"
        if value in {"side", "baseline", "diagonal", "rear", "front"}:
            return value
        return "unknown"

    def _angle_camera_confidence(self, camera_angle: str | None) -> TechnicalFeedbackConfidenceLevel:
        normalized = self._canonical_camera_angle(camera_angle)
        if normalized == "side":
            return "high"
        if normalized == "baseline":
            return "medium"
        if normalized == "diagonal":
            return "low"
        return "insufficient"

    def _rule_camera_confidence(self, rule: TechnicalFeedbackRuleConfig) -> TechnicalFeedbackConfidenceLevel:
        camera_angle = getattr(self, "_current_camera_angle", "unknown")
        normalized = self._canonical_camera_angle(camera_angle)
        if not rule.supported_camera_angles:
            return "high"
        if normalized in rule.supported_camera_angles:
            if normalized == "side":
                return "high"
            if normalized == "baseline":
                return "medium"
            if normalized == "diagonal":
                return "low"
            return "high"
        if normalized in {"rear", "front", "unknown"}:
            return "insufficient"
        return "low"

    def _cap_confidence(
        self,
        confidence: TechnicalFeedbackConfidenceLevel,
        ceiling: TechnicalFeedbackConfidenceLevel | None,
    ) -> TechnicalFeedbackConfidenceLevel:
        if ceiling is None:
            return confidence
        return self._lowest_confidence(confidence, ceiling)

    def _evaluate_rule(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
    ) -> TechnicalFeedbackDecision:
        if rule.id == "limited_knee_bend":
            return self._evaluate_knee_bend(rule, stroke, pose)

        capability = self._capability_for_rule(stroke.analysis_capabilities, rule.id)
        if capability is None:
            return self._withheld(rule, "capability not available", [rule.metric], {"reason": "missing capability"})

        if self._confidence_rank(capability.confidence_level) < self._confidence_rank(rule.minimum_capability_confidence):
            return self._withheld(
                rule,
                f"capability confidence below the experimental minimum ({capability.confidence_level} < {rule.minimum_capability_confidence})",
                rule.required_capabilities or [rule.metric],
                {"capability_confidence": capability.confidence_level},
            )

        handlers = {
            "late_preparation": self._evaluate_late_preparation,
            "limited_shoulder_rotation": self._evaluate_limited_shoulder_rotation,
            "limited_follow_through_rotation": self._evaluate_limited_follow_through_rotation,
            "limited_hip_rotation": self._evaluate_limited_hip_rotation,
            "shoulder_hip_sequence_difference": self._evaluate_shoulder_hip_sequence_difference,
            "arm_too_folded_at_contact": self._evaluate_arm_extension,
            "arm_near_full_extension_at_contact": self._evaluate_arm_extension,
            "unstable_finish": self._evaluate_unstable_finish,
            "slow_recovery": self._evaluate_slow_recovery,
        }
        handler = handlers.get(rule.id)
        if handler is None:
            return self._withheld(rule, "rule handler missing", [rule.metric], {"reason": "unsupported rule"})

        return handler(rule, stroke, pose, capability)

    def _evaluate_late_preparation(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        phases = stroke.phases
        if not self._phase_ready(phases, ["ready", "preparation_start", "contact_estimate"]):
            return self._withheld(rule, "stroke phases are incomplete", ["ready", "preparation_start", "contact_estimate"], {})

        ready = phases.ready
        prep = phases.preparation_start
        contact = phases.contact_estimate
        if ready is None or prep is None or contact is None or contact.frame_index in {None, 0}:
            return self._withheld(rule, "stroke phase timestamps are incomplete", ["ready", "preparation_start", "contact_estimate"], {})

        ratio = self._ratio(prep.frame_index, contact.frame_index)
        if ratio is None or ratio <= rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        if phases.confidence_level in {"low", "insufficient"} or self._confidence_rank(dependency_confidence) < self._confidence_rank("medium"):
            return self._withheld(
                rule,
                "stroke phase confidence is too weak for preparation timing interpretation",
                ["stroke_phases", "preparation_start", "contact_estimate"],
                {
                    "phase_confidence": phases.confidence_level,
                    "dependency_confidence": dependency_confidence,
                },
            )

        confidence = self._cap_confidence(
            self._build_confidence(capability, phases.confidence_level, pose, distance=self._distance_score(ratio, rule.provisional_threshold, invert=False)),
            dependency_confidence,
        )
        kind = "possible_observation" if confidence == "low" else "finding"
        return TechnicalFeedbackDecision(
            findings=[
                self._finding(
                    rule=rule,
                    title="נראה שההכנה מתחילה מאוחר",
                    kind=kind,
                    classification=self._classification_from_confidence(confidence),
                    confidence=confidence,
                    importance=self._importance_from_confidence(confidence),
                    observation=f"הכנת החבטה מתחילה ב-{ratio:.0%} מהדרך עד המגע, מעל הסף הזמני של {rule.provisional_threshold:.0%}.",
                    metric_value=ratio,
                    primary_event=prep,
                    evidence=[
                        self._evidence("ready", ready, value=True),
                        self._evidence("preparation_start", prep, value=ratio),
                        self._evidence("contact_estimate", contact, value=contact.frame_index),
                    ],
                    phase_references=[
                        self._phase_ref("ready", ready),
                        self._phase_ref("preparation_start", prep),
                        self._phase_ref("contact_estimate", contact),
                    ],
                    stroke=stroke,
                    limitations=self._limitations(confidence, capability, phases.confidence_level, pose),
                    supporting_details={"preparation_ratio": round(ratio, 4)},
                    confidence_source=capability.confidence_source,
                    confidence_ceiling=dependency_confidence,
                    dependency_confidence=dependency_confidence,
                    camera_angle=self._current_camera_angle,
                )
            ]
        )

    def _evaluate_limited_shoulder_rotation(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None:
            return self._withheld(rule, "rotation metrics are missing", [rule.metric], {})

        prep = metrics.shoulder_line_orientation.preparation
        contact = metrics.shoulder_line_orientation.contact
        if prep is None or contact is None:
            return self._withheld(rule, "shoulder rotation measurements are incomplete", [rule.metric], {})

        delta = self._angle_delta(prep, contact)
        if delta is None or delta > rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, stroke.phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        confidence = self._cap_confidence(
            self._build_confidence(
                capability,
                stroke.phases.confidence_level,
                pose,
                distance=self._distance_score(delta, rule.provisional_threshold, invert=False),
                continuity=self._continuity_score(capability),
            ),
            dependency_confidence,
        )
        follow = metrics.shoulder_line_orientation.follow_through
        evidence = [self._evidence("preparation", prep, value=prep.value_degrees), self._evidence("contact", contact, value=contact.value_degrees)]
        if follow is not None:
            evidence.append(self._evidence("follow_through", follow, value=follow.value_degrees))
        if confidence == "insufficient":
            return self._withheld(rule, "shoulder rotation confidence is insufficient", [rule.metric], {"dependency_confidence": dependency_confidence})
        if confidence == "low":
            return TechnicalFeedbackDecision(
                possible_observations=[
                    self._finding(
                        rule=rule,
                        title="סיבוב הכתפיים מוגבל",
                        kind="possible_observation",
                        classification="possible_observation",
                        confidence=confidence,
                        importance="secondary",
                        observation=f"שינוי הכתפיים בין ההכנה למגע נמדד כ-{delta:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°.",
                        metric_value=delta,
                        primary_event=contact,
                        evidence=evidence,
                        phase_references=[
                            self._phase_ref("preparation", prep),
                            self._phase_ref("contact", contact),
                            self._phase_ref("follow_through", follow),
                        ],
                        stroke=stroke,
                        limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
                        supporting_details={"preparation_to_contact_delta": round(delta, 4)},
                        confidence_source=capability.confidence_source,
                        confidence_ceiling=dependency_confidence,
                        dependency_confidence=dependency_confidence,
                        camera_angle=self._current_camera_angle,
                    )
                ]
            )
        return TechnicalFeedbackDecision(
            findings=[
                self._finding(
                    rule=rule,
                    title="סיבוב הכתפיים מוגבל",
                    kind="finding",
                    classification=self._classification_from_confidence(confidence),
                    confidence=confidence,
                    importance=self._importance_from_confidence(confidence),
                    observation=f"שינוי הכתפיים בין ההכנה למגע נמדד כ-{delta:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°.",
                    metric_value=delta,
                    primary_event=contact,
                    evidence=evidence,
                    phase_references=[
                        self._phase_ref("preparation", prep),
                        self._phase_ref("contact", contact),
                        self._phase_ref("follow_through", follow),
                    ],
                    stroke=stroke,
                    limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
                    supporting_details={"preparation_to_contact_delta": round(delta, 4)},
                    confidence_source=capability.confidence_source,
                    confidence_ceiling=dependency_confidence,
                    dependency_confidence=dependency_confidence,
                    camera_angle=self._current_camera_angle,
                )
            ]
        )

    def _evaluate_limited_follow_through_rotation(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None:
            return self._withheld(rule, "rotation metrics are missing", [rule.metric], {})

        contact = metrics.shoulder_line_orientation.contact
        follow = metrics.shoulder_line_orientation.follow_through
        if contact is None or follow is None:
            return self._withheld(rule, "follow-through rotation measurements are incomplete", [rule.metric], {})

        delta = self._angle_delta(contact, follow)
        if delta is None or delta > rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, stroke.phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        confidence = self._cap_confidence(
            self._build_confidence(
                capability,
                stroke.phases.confidence_level,
                pose,
                distance=self._distance_score(delta, rule.provisional_threshold, invert=False),
                continuity=self._continuity_score(capability),
            ),
            dependency_confidence,
        )
        if confidence == "insufficient":
            return self._withheld(rule, "shoulder rotation confidence is insufficient", [rule.metric], {"dependency_confidence": dependency_confidence})
        if confidence == "low":
            return TechnicalFeedbackDecision(
                possible_observations=[
                    self._finding(
                        rule=rule,
                        title="סיבוב הכתפיים בהמשך קטן מדי",
                        kind="possible_observation",
                        classification="possible_observation",
                        confidence=confidence,
                        importance="secondary",
                        observation=f"שינוי הכתפיים מהמגע אל ההמשך נמדד כ-{delta:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°.",
                        metric_value=delta,
                        primary_event=follow,
                        evidence=[
                            self._evidence("contact", contact, value=contact.value_degrees),
                            self._evidence("follow_through", follow, value=follow.value_degrees),
                        ],
                        phase_references=[self._phase_ref("contact", contact), self._phase_ref("follow_through", follow)],
                        stroke=stroke,
                        limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
                        supporting_details={"contact_to_follow_delta": round(delta, 4)},
                        confidence_source=capability.confidence_source,
                        confidence_ceiling=dependency_confidence,
                        dependency_confidence=dependency_confidence,
                        camera_angle=self._current_camera_angle,
                    )
                ]
            )
        return TechnicalFeedbackDecision(
            findings=[
                self._finding(
                    rule=rule,
                    title="סיבוב הכתפיים בהמשך קטן מדי",
                    kind="finding",
                    classification=self._classification_from_confidence(confidence),
                    confidence=confidence,
                    importance=self._importance_from_confidence(confidence),
                    observation=f"שינוי הכתפיים מהמגע אל ההמשך נמדד כ-{delta:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°.",
                    metric_value=delta,
                    primary_event=follow,
                    evidence=[
                        self._evidence("contact", contact, value=contact.value_degrees),
                        self._evidence("follow_through", follow, value=follow.value_degrees),
                    ],
                    phase_references=[self._phase_ref("contact", contact), self._phase_ref("follow_through", follow)],
                    stroke=stroke,
                    limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
                    supporting_details={"contact_to_follow_delta": round(delta, 4)},
                    confidence_source=capability.confidence_source,
                    confidence_ceiling=dependency_confidence,
                    dependency_confidence=dependency_confidence,
                    camera_angle=self._current_camera_angle,
                )
            ]
        )

    def _evaluate_limited_hip_rotation(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None:
            return self._withheld(rule, "hip rotation metrics are missing", [rule.metric], {})

        prep = metrics.hip_line_orientation.preparation
        contact = metrics.hip_line_orientation.contact
        if prep is None or contact is None:
            return self._withheld(rule, "hip rotation measurements are incomplete", [rule.metric], {})

        delta = self._angle_delta(prep, contact)
        if delta is None or delta > rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, stroke.phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        confidence = self._cap_confidence(
            self._build_confidence(
                capability,
                stroke.phases.confidence_level,
                pose,
                distance=self._distance_score(delta, rule.provisional_threshold, invert=False),
                continuity=self._continuity_score(capability),
            ),
            dependency_confidence,
        )
        if confidence == "insufficient":
            return self._withheld(rule, "hip rotation confidence is insufficient", [rule.metric], {"dependency_confidence": dependency_confidence})
        if confidence == "low":
            return TechnicalFeedbackDecision(
                possible_observations=[
                    self._finding(
                        rule=rule,
                        title="סיבוב הירכיים מוגבל",
                        kind="possible_observation",
                        classification="possible_observation",
                        confidence=confidence,
                        importance="secondary",
                        observation=f"שינוי הירכיים בין ההכנה למגע נמדד כ-{delta:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°.",
                        metric_value=delta,
                        primary_event=contact,
                        evidence=[self._evidence("preparation", prep, value=prep.value_degrees), self._evidence("contact", contact, value=contact.value_degrees)],
                        phase_references=[self._phase_ref("preparation", prep), self._phase_ref("contact", contact)],
                        stroke=stroke,
                        limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
                        supporting_details={"preparation_to_contact_delta": round(delta, 4)},
                        confidence_source=capability.confidence_source,
                        confidence_ceiling=dependency_confidence,
                        dependency_confidence=dependency_confidence,
                        camera_angle=self._current_camera_angle,
                    )
                ]
            )
        return TechnicalFeedbackDecision(
            findings=[
                self._finding(
                    rule=rule,
                    title="סיבוב הירכיים מוגבל",
                    kind="finding",
                    classification=self._classification_from_confidence(confidence),
                    confidence=confidence,
                    importance=self._importance_from_confidence(confidence),
                    observation=f"שינוי הירכיים בין ההכנה למגע נמדד כ-{delta:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°.",
                    metric_value=delta,
                    primary_event=contact,
                    evidence=[self._evidence("preparation", prep, value=prep.value_degrees), self._evidence("contact", contact, value=contact.value_degrees)],
                    phase_references=[self._phase_ref("preparation", prep), self._phase_ref("contact", contact)],
                    stroke=stroke,
                    limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
                    supporting_details={"preparation_to_contact_delta": round(delta, 4)},
                    confidence_source=capability.confidence_source,
                    confidence_ceiling=dependency_confidence,
                    dependency_confidence=dependency_confidence,
                    camera_angle=self._current_camera_angle,
                )
            ]
        )

    def _evaluate_shoulder_hip_sequence_difference(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None:
            return self._withheld(rule, "rotation metrics are missing", [rule.metric], {})

        shoulder_capability = stroke.analysis_capabilities.shoulder_rotation
        hip_capability = stroke.analysis_capabilities.hip_rotation
        if (
            shoulder_capability is None
            or hip_capability is None
            or not shoulder_capability.available
            or not hip_capability.available
            or self._confidence_rank(shoulder_capability.confidence_level) < self._confidence_rank(rule.minimum_capability_confidence)
            or self._confidence_rank(hip_capability.confidence_level) < self._confidence_rank(rule.minimum_capability_confidence)
        ):
            return self._withheld(
                rule,
                "shoulder and hip rotation are not both reliable enough",
                ["shoulder_rotation", "hip_rotation"],
                {
                    "shoulder_confidence": shoulder_capability.confidence_level if shoulder_capability is not None else "insufficient",
                    "hip_confidence": hip_capability.confidence_level if hip_capability is not None else "insufficient",
                },
            )

        combined_confidence = self._lowest_confidence(shoulder_capability.confidence_level, hip_capability.confidence_level)
        combined_capability = StrokeCapabilityAssessment(
            available=True,
            confidence_level=combined_confidence,
            required_landmarks=sorted({*shoulder_capability.required_landmarks, *hip_capability.required_landmarks}),
            warnings=self._dedupe_strings([*shoulder_capability.warnings, *hip_capability.warnings]),
            reasons=self._dedupe_strings([*shoulder_capability.reasons, *hip_capability.reasons]),
            diagnostics=[*shoulder_capability.diagnostics, *hip_capability.diagnostics],
        )

        shoulder_prep = metrics.shoulder_line_orientation.preparation
        shoulder_contact = metrics.shoulder_line_orientation.contact
        hip_prep = metrics.hip_line_orientation.preparation
        hip_contact = metrics.hip_line_orientation.contact
        if shoulder_prep is None or shoulder_contact is None or hip_prep is None or hip_contact is None:
            return self._withheld(rule, "shoulder and hip sequence measurements are incomplete", [rule.metric], {})

        shoulder_delta = self._angle_delta(shoulder_prep, shoulder_contact)
        hip_delta = self._angle_delta(hip_prep, hip_contact)
        if shoulder_delta is None or hip_delta is None:
            return TechnicalFeedbackDecision()

        gap = abs(shoulder_delta - hip_delta)
        if gap <= rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(combined_capability.confidence_level, stroke.phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        if self._confidence_rank(dependency_confidence) < self._confidence_rank("medium"):
            return TechnicalFeedbackDecision(
                neutral_measurements=[
                    self._finding(
                        rule=rule,
                        title="upper_lower_body_separation_observed",
                        kind="neutral_measurement",
                        classification="neutral_measurement",
                        confidence=dependency_confidence,
                        importance="neutral",
                        observation=f"נמדד הבדל בתזמון או בזווית בין תנועת הכתפיים לתנועת האגן: כתפיים {shoulder_delta:.1f}°, אגן {hip_delta:.1f}°, הפרש {gap:.1f}°.",
                        metric_value=gap,
                        primary_event=shoulder_contact,
                        evidence=[
                            self._evidence("shoulder preparation", shoulder_prep, value=shoulder_prep.value_degrees),
                            self._evidence("shoulder contact", shoulder_contact, value=shoulder_contact.value_degrees),
                            self._evidence("hip preparation", hip_prep, value=hip_prep.value_degrees),
                            self._evidence("hip contact", hip_contact, value=hip_contact.value_degrees),
                        ],
                        phase_references=[
                            self._phase_ref("shoulder_preparation", shoulder_prep),
                            self._phase_ref("shoulder_contact", shoulder_contact),
                            self._phase_ref("hip_preparation", hip_prep),
                            self._phase_ref("hip_contact", hip_contact),
                        ],
                        stroke=stroke,
                        limitations=self._limitations(dependency_confidence, combined_capability, stroke.phases.confidence_level, pose)
                        + ["image-plane estimates only"],
                        supporting_details={
                            "shoulder_rotation_delta": round(shoulder_delta, 4),
                            "hip_rotation_delta": round(hip_delta, 4),
                            "rotation_gap": round(gap, 4),
                            "camera_angle": self._current_camera_angle,
                            "legacy_derived": combined_capability.confidence_source == "legacy_derived",
                        },
                        confidence_source=combined_capability.confidence_source,
                        confidence_ceiling=dependency_confidence,
                        dependency_confidence=dependency_confidence,
                        camera_angle=self._current_camera_angle,
                    )
                ]
            )

        return TechnicalFeedbackDecision(
            neutral_measurements=[
                self._finding(
                    rule=rule,
                    title="upper_lower_body_separation_observed",
                    kind="neutral_measurement",
                    classification="neutral_measurement",
                    confidence=self._cap_confidence(
                        self._build_confidence(
                            combined_capability,
                            stroke.phases.confidence_level,
                            pose,
                            distance=self._distance_score(gap, rule.provisional_threshold, invert=False),
                            continuity=self._continuity_score(combined_capability),
                        ),
                        dependency_confidence,
                    ),
                    importance="neutral",
                    observation=f"נמדד הבדל בתזמון או בזווית בין תנועת הכתפיים לתנועת האגן: כתפיים {shoulder_delta:.1f}°, אגן {hip_delta:.1f}°, הפרש {gap:.1f}°.",
                    metric_value=gap,
                    primary_event=shoulder_contact,
                    evidence=[
                        self._evidence("shoulder preparation", shoulder_prep, value=shoulder_prep.value_degrees),
                        self._evidence("shoulder contact", shoulder_contact, value=shoulder_contact.value_degrees),
                        self._evidence("hip preparation", hip_prep, value=hip_prep.value_degrees),
                        self._evidence("hip contact", hip_contact, value=hip_contact.value_degrees),
                    ],
                    phase_references=[
                        self._phase_ref("shoulder_preparation", shoulder_prep),
                        self._phase_ref("shoulder_contact", shoulder_contact),
                        self._phase_ref("hip_preparation", hip_prep),
                        self._phase_ref("hip_contact", hip_contact),
                    ],
                    stroke=stroke,
                    limitations=self._limitations(dependency_confidence, combined_capability, stroke.phases.confidence_level, pose)
                    + ["image-plane estimates only"],
                    supporting_details={
                        "shoulder_rotation_delta": round(shoulder_delta, 4),
                        "hip_rotation_delta": round(hip_delta, 4),
                        "rotation_gap": round(gap, 4),
                        "camera_angle": self._current_camera_angle,
                        "legacy_derived": combined_capability.confidence_source == "legacy_derived",
                    },
                    confidence_source=combined_capability.confidence_source,
                    confidence_ceiling=dependency_confidence,
                    dependency_confidence=dependency_confidence,
                    camera_angle=self._current_camera_angle,
                )
            ]
        )

    def _evaluate_knee_bend(self, rule: TechnicalFeedbackRuleConfig, stroke: StrokeAnalysisSummary, pose: PoseAnalysisSummary | None) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None:
            return self._withheld(rule, "knee angle metrics are missing", [rule.metric], {})

        findings: list[TechnicalFeedbackFinding] = []
        possible_observations: list[TechnicalFeedbackFinding] = []
        withheld: list[TechnicalFeedbackWithheldFinding] = []
        contact_phase = self._phase_event_at_contact(stroke.phases)
        for side, measurement, side_capability in (
            ("left", metrics.knee_angles_at_contact.left, stroke.analysis_capabilities.knee_angles.left),
            ("right", metrics.knee_angles_at_contact.right, stroke.analysis_capabilities.knee_angles.right),
        ):
            if side_capability is None or not side_capability.available or self._confidence_rank(side_capability.confidence_level) < self._confidence_rank(rule.minimum_capability_confidence):
                withheld.append(
                    TechnicalFeedbackWithheldFinding(
                        rule_id=f"{rule.id}_{side}",
                        family=rule.family,
                        title=f"{side.capitalize()} knee bend",
                        reason="knee capability is not available with sufficient confidence",
                        missing_capabilities=[f"knee_angles.{side}"],
                        limitations=["המדידה הזו דורשת ברך נראית ואמינה באותו צד."],
                        supporting_details={"side": side},
                    )
                )
                continue

            if measurement is None or measurement.value_degrees is None:
                withheld.append(
                    TechnicalFeedbackWithheldFinding(
                        rule_id=f"{rule.id}_{side}",
                        family=rule.family,
                        title=f"{side.capitalize()} knee bend",
                        reason="knee angle measurement is missing",
                        missing_capabilities=[f"knee_angles.{side}"],
                        limitations=["אין מדידת זווית זמינה עבור הברך הזו."],
                        supporting_details={"side": side},
                    )
                )
                continue

            if measurement.value_degrees < rule.provisional_threshold:
                continue

            camera_confidence = self._rule_camera_confidence(rule)
            dependency_confidence = self._lowest_confidence(
                self._lowest_confidence(side_capability.confidence_level, stroke.phases.confidence_level),
                self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
            )
            confidence = self._cap_confidence(
                self._build_confidence(
                    side_capability,
                    stroke.phases.confidence_level,
                    pose,
                    distance=self._distance_score(measurement.value_degrees, rule.provisional_threshold, invert=False),
                    continuity=self._continuity_score(side_capability),
                ),
                dependency_confidence,
            )
            if confidence == "insufficient":
                withheld.append(
                    TechnicalFeedbackWithheldFinding(
                        rule_id=f"{rule.id}_{side}",
                        family=rule.family,
                        title=f"{side.capitalize()} knee bend",
                        reason="knee confidence is insufficient",
                        missing_capabilities=[f"knee_angles.{side}"],
                        limitations=["המדידה הזו דורשת ברך נראית ואמינה באותו צד."],
                        supporting_details={"side": side, "dependency_confidence": dependency_confidence},
                    )
                )
                continue

            finding = self._finding(
                rule=rule,
                title=f"{side.capitalize()} knee bend limited",
                kind="possible_observation" if confidence == "low" else "finding",
                classification="possible_observation" if confidence == "low" else self._classification_from_confidence(confidence),
                confidence=confidence,
                importance="secondary" if confidence == "low" else self._importance_from_confidence(confidence),
                observation=f"זווית הברך ב-{side} נמדדה כ-{measurement.value_degrees:.1f}°, מעל הסף הזמני של {rule.provisional_threshold:.1f}°.",
                metric_value=measurement.value_degrees,
                primary_event=measurement,
                evidence=[self._evidence(f"{side} knee at contact", measurement, value=measurement.value_degrees)],
                phase_references=[self._phase_ref("contact", contact_phase)],
                stroke=stroke,
                limitations=self._limitations(confidence, side_capability, stroke.phases.confidence_level, pose),
                supporting_details={"side": side, "knee_angle_at_contact": round(measurement.value_degrees, 4)},
                rule_id_override=f"{rule.id}_{side}",
                confidence_source=side_capability.confidence_source,
                confidence_ceiling=dependency_confidence,
                dependency_confidence=dependency_confidence,
                camera_angle=self._current_camera_angle,
            )
            if confidence == "low":
                possible_observations.append(finding)
            else:
                findings.append(finding)

        return TechnicalFeedbackDecision(findings=findings, possible_observations=possible_observations, withheld=withheld)

    def _evaluate_arm_extension(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        contact = stroke.phases.contact_estimate
        if metrics is None or contact is None:
            return self._withheld(rule, "elbow angle measurements are incomplete", [rule.metric], {})

        measurement = metrics.dominant_elbow_angle.contact
        if measurement is None or measurement.value_degrees is None:
            return self._withheld(rule, "dominant elbow angle at contact is missing", [rule.metric], {})

        if rule.id == "arm_too_folded_at_contact":
            if measurement.value_degrees >= rule.provisional_threshold:
                return TechnicalFeedbackDecision()
            observation = f"זווית המרפק הדומיננטי במגע נמדדה כ-{measurement.value_degrees:.1f}°, מתחת לסף הזמני של {rule.provisional_threshold:.1f}°."
            title = "הזרוע כפופה מדי במגע"
            comparison = rule.comparison
        else:
            if measurement.value_degrees <= rule.provisional_threshold:
                return TechnicalFeedbackDecision()
            observation = f"זווית המרפק הדומיננטי במגע נמדדה כ-{measurement.value_degrees:.1f}°, מעל הסף הזמני של {rule.provisional_threshold:.1f}°."
            title = "הזרוע כמעט ישרה לגמרי במגע"
            comparison = rule.comparison

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, stroke.phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        confidence = self._build_confidence(
            capability,
            stroke.phases.confidence_level,
            pose,
            distance=self._distance_score(
                measurement.value_degrees,
                rule.provisional_threshold,
                invert=rule.id == "arm_too_folded_at_contact",
            ),
            continuity=self._continuity_score(capability),
        )
        confidence = self._cap_confidence(confidence, dependency_confidence)
        if confidence == "insufficient":
            return self._withheld(rule, "dominant elbow angle confidence is insufficient", [rule.metric], {"dependency_confidence": dependency_confidence})

        finding = self._finding(
            rule=rule,
            title=title,
            kind="possible_observation" if confidence == "low" else "finding",
            classification="possible_observation" if confidence == "low" else self._classification_from_confidence(confidence),
            confidence=confidence,
            importance="secondary" if confidence == "low" else self._importance_from_confidence(confidence),
            observation=observation,
            metric_value=measurement.value_degrees,
            primary_event=measurement,
            evidence=[
                self._evidence(
                    "preparation",
                    metrics.dominant_elbow_angle.preparation,
                    value=metrics.dominant_elbow_angle.preparation.value_degrees if metrics.dominant_elbow_angle.preparation else None,
                ),
                self._evidence("contact", measurement, value=measurement.value_degrees),
                self._evidence(
                    "follow_through",
                    metrics.dominant_elbow_angle.follow_through,
                    value=metrics.dominant_elbow_angle.follow_through.value_degrees if metrics.dominant_elbow_angle.follow_through else None,
                ),
            ],
            phase_references=[
                self._phase_ref("preparation", self._phase_event_at_preparation(stroke.phases)),
                self._phase_ref("contact", contact),
                self._phase_ref("follow_through", stroke.phases.follow_through_peak),
            ],
            stroke=stroke,
            limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
            supporting_details={"dominant_hand": stroke.dominant_hand, "contact_elbow_angle": round(measurement.value_degrees, 4)},
            comparison_override=comparison,
            confidence_source=capability.confidence_source,
            confidence_ceiling=dependency_confidence,
            dependency_confidence=dependency_confidence,
            camera_angle=self._current_camera_angle,
        )
        if confidence == "low":
            return TechnicalFeedbackDecision(possible_observations=[finding])
        return TechnicalFeedbackDecision(findings=[finding])

    def _evaluate_unstable_finish(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None or not metrics.balance_proxy.reliable or metrics.balance_proxy.proxy_score is None:
            return self._withheld(rule, "balance proxy is not reliable enough", [rule.metric], {})

        if metrics.balance_proxy.proxy_score >= rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, stroke.phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        confidence = self._build_confidence(
            capability,
            stroke.phases.confidence_level,
            pose,
            distance=self._distance_score(metrics.balance_proxy.proxy_score, rule.provisional_threshold, invert=True),
            continuity=metrics.balance_proxy.proxy_score,
        )
        confidence = self._cap_confidence(confidence, dependency_confidence)
        if confidence == "insufficient":
            return self._withheld(rule, "balance confidence is insufficient", [rule.metric], {"dependency_confidence": dependency_confidence})
        primary = stroke.phases.recovery or stroke.phases.follow_through_peak or stroke.phases.contact_estimate
        finding = self._finding(
            rule=rule,
            title="הסיום נראה לא יציב",
            kind="possible_observation" if confidence == "low" else "finding",
            classification="possible_observation" if confidence == "low" else self._classification_from_confidence(confidence),
            confidence=confidence,
            importance="secondary" if confidence == "low" else self._importance_from_confidence(confidence),
            observation=f"ציון היציבות בסיום נמדד כ-{metrics.balance_proxy.proxy_score:.2f}, מתחת לסף הזמני של {rule.provisional_threshold:.2f}.",
            metric_value=metrics.balance_proxy.proxy_score,
            primary_event=primary,
            evidence=[
                self._evidence("contact", stroke.phases.contact_estimate, value=metrics.balance_proxy.body_center_drift),
                self._evidence("recovery", stroke.phases.recovery, value=metrics.balance_proxy.proxy_score),
            ],
            phase_references=[self._phase_ref("contact", stroke.phases.contact_estimate), self._phase_ref("recovery", stroke.phases.recovery)],
            stroke=stroke,
            limitations=self._limitations(confidence, capability, stroke.phases.confidence_level, pose),
            supporting_details={
                "body_center_drift": metrics.balance_proxy.body_center_drift or 0.0,
                "foot_support_range": metrics.balance_proxy.foot_support_range or 0.0,
            },
            confidence_source=capability.confidence_source,
            confidence_ceiling=dependency_confidence,
            dependency_confidence=dependency_confidence,
            camera_angle=self._current_camera_angle,
        )
        if confidence == "low":
            return TechnicalFeedbackDecision(possible_observations=[finding])
        return TechnicalFeedbackDecision(findings=[finding])

    def _evaluate_slow_recovery(
        self,
        rule: TechnicalFeedbackRuleConfig,
        stroke: StrokeAnalysisSummary,
        pose: PoseAnalysisSummary | None,
        capability: StrokeCapabilityAssessment,
    ) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        phases = stroke.phases
        if metrics is None or metrics.recovery_duration_ms is None or phases.recovery is None or phases.follow_through_peak is None:
            return self._withheld(rule, "recovery timing is not reliable enough", [rule.metric], {})

        if metrics.recovery_duration_ms <= rule.provisional_threshold:
            return TechnicalFeedbackDecision()

        camera_confidence = self._rule_camera_confidence(rule)
        dependency_confidence = self._lowest_confidence(
            self._lowest_confidence(capability.confidence_level, phases.confidence_level),
            self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
        )
        confidence = self._build_confidence(
            capability,
            phases.confidence_level,
            pose,
            distance=self._distance_score(metrics.recovery_duration_ms, rule.provisional_threshold, invert=False),
            continuity=self._continuity_score(capability),
        )
        confidence = self._cap_confidence(confidence, dependency_confidence)
        if confidence == "insufficient":
            return self._withheld(rule, "recovery confidence is insufficient", [rule.metric], {"dependency_confidence": dependency_confidence})
        finding = self._finding(
            rule=rule,
            title="ההתאוששות איטית",
            kind="possible_observation" if confidence == "low" else "finding",
            classification="possible_observation" if confidence == "low" else self._classification_from_confidence(confidence),
            confidence=confidence,
            importance="secondary" if confidence == "low" else self._importance_from_confidence(confidence),
            observation=f"משך ההתאוששות נמדד כ-{metrics.recovery_duration_ms} מ\"ש, מעל הסף הזמני של {int(rule.provisional_threshold)} מ\"ש.",
            metric_value=float(metrics.recovery_duration_ms),
            primary_event=phases.recovery,
            evidence=[
                self._evidence("follow_through", phases.follow_through_peak, value=phases.follow_through_peak.frame_index if phases.follow_through_peak else None),
                self._evidence("recovery", phases.recovery, value=metrics.recovery_duration_ms),
            ],
            phase_references=[self._phase_ref("follow_through", phases.follow_through_peak), self._phase_ref("recovery", phases.recovery)],
            stroke=stroke,
            limitations=self._limitations(confidence, capability, phases.confidence_level, pose),
            supporting_details={"recovery_duration_ms": float(metrics.recovery_duration_ms)},
            confidence_source=capability.confidence_source,
            confidence_ceiling=dependency_confidence,
            dependency_confidence=dependency_confidence,
            camera_angle=self._current_camera_angle,
        )
        if confidence == "low":
            return TechnicalFeedbackDecision(possible_observations=[finding])
        return TechnicalFeedbackDecision(findings=[finding])

    def _evaluate_knee_bend(self, rule: TechnicalFeedbackRuleConfig, stroke: StrokeAnalysisSummary, pose: PoseAnalysisSummary | None) -> TechnicalFeedbackDecision:
        metrics = stroke.metrics
        if metrics is None:
            return self._withheld(rule, "knee angle metrics are missing", [rule.metric], {})

        findings: list[TechnicalFeedbackFinding] = []
        possible_observations: list[TechnicalFeedbackFinding] = []
        withheld: list[TechnicalFeedbackWithheldFinding] = []
        contact_phase = self._phase_event_at_contact(stroke.phases)
        for side, measurement, side_capability in (
            ("left", metrics.knee_angles_at_contact.left, stroke.analysis_capabilities.knee_angles.left),
            ("right", metrics.knee_angles_at_contact.right, stroke.analysis_capabilities.knee_angles.right),
        ):
            if side_capability is None or not side_capability.available or self._confidence_rank(side_capability.confidence_level) < self._confidence_rank(rule.minimum_capability_confidence):
                withheld.append(
                    TechnicalFeedbackWithheldFinding(
                        rule_id=f"{rule.id}_{side}",
                        family=rule.family,
                        title=f"{side.capitalize()} knee bend",
                        reason="knee capability is not available with sufficient confidence",
                        missing_capabilities=[f"knee_angles.{side}"],
                        limitations=["המדידה הזו דורשת ברך נראית ואמינה באותו צד."],
                        supporting_details={"side": side},
                    )
                )
                continue

            if measurement is None or measurement.value_degrees is None:
                withheld.append(
                    TechnicalFeedbackWithheldFinding(
                        rule_id=f"{rule.id}_{side}",
                        family=rule.family,
                        title=f"{side.capitalize()} knee bend",
                        reason="knee angle measurement is missing",
                        missing_capabilities=[f"knee_angles.{side}"],
                        limitations=["אין מדידת זווית זמינה עבור הברך הזו."],
                        supporting_details={"side": side},
                    )
                )
                continue

            if measurement.value_degrees < rule.provisional_threshold:
                continue

            camera_confidence = self._rule_camera_confidence(rule)
            dependency_confidence = self._lowest_confidence(
                self._lowest_confidence(side_capability.confidence_level, stroke.phases.confidence_level),
                self._lowest_confidence(pose.confidence_level if pose is not None else "insufficient", camera_confidence),
            )
            confidence = self._cap_confidence(
                self._build_confidence(
                    side_capability,
                    stroke.phases.confidence_level,
                    pose,
                    distance=self._distance_score(measurement.value_degrees, rule.provisional_threshold, invert=False),
                    continuity=self._continuity_score(side_capability),
                ),
                dependency_confidence,
            )
            if confidence == "insufficient":
                withheld.append(
                    TechnicalFeedbackWithheldFinding(
                        rule_id=f"{rule.id}_{side}",
                        family=rule.family,
                        title=f"{side.capitalize()} knee bend",
                        reason="knee confidence is insufficient",
                        missing_capabilities=[f"knee_angles.{side}"],
                        limitations=["המדידה הזו דורשת ברך נראית ואמינה באותו צד."],
                        supporting_details={"side": side, "dependency_confidence": dependency_confidence},
                    )
                )
                continue

            finding = self._finding(
                rule=rule,
                title=f"{side.capitalize()} knee bend limited",
                kind="possible_observation" if confidence == "low" else "finding",
                classification="possible_observation" if confidence == "low" else self._classification_from_confidence(confidence),
                confidence=confidence,
                importance="secondary" if confidence == "low" else self._importance_from_confidence(confidence),
                observation=f"זווית הברך ב-{side} נמדדה כ-{measurement.value_degrees:.1f}°, מעל הסף הזמני של {rule.provisional_threshold:.1f}°.",
                metric_value=measurement.value_degrees,
                primary_event=measurement,
                evidence=[self._evidence(f"{side} knee at contact", measurement, value=measurement.value_degrees)],
                phase_references=[self._phase_ref("contact", contact_phase)],
                stroke=stroke,
                limitations=self._limitations(confidence, side_capability, stroke.phases.confidence_level, pose),
                supporting_details={"side": side, "knee_angle_at_contact": round(measurement.value_degrees, 4)},
                rule_id_override=f"{rule.id}_{side}",
                confidence_source=side_capability.confidence_source,
                confidence_ceiling=dependency_confidence,
                dependency_confidence=dependency_confidence,
                camera_angle=self._current_camera_angle,
            )
            if confidence == "low":
                possible_observations.append(finding)
            else:
                findings.append(finding)

        return TechnicalFeedbackDecision(findings=findings, possible_observations=possible_observations, withheld=withheld)

    def _withheld(
        self,
        rule: TechnicalFeedbackRuleConfig,
        reason: str,
        missing_capabilities: list[str],
        supporting_details: dict[str, float | str | bool],
    ) -> TechnicalFeedbackDecision:
        return TechnicalFeedbackDecision(
            withheld=[
                TechnicalFeedbackWithheldFinding(
                    rule_id=rule.id,
                    family=rule.family,
                    title=self._title_for_rule(rule.id),
                    reason=reason,
                    missing_capabilities=missing_capabilities,
                    limitations=[rule.explanation],
                    supporting_details=supporting_details,
                )
            ]
        )

    def _finding(
        self,
        *,
        rule: TechnicalFeedbackRuleConfig,
        title: str,
        kind: TechnicalFeedbackOutputKind,
        classification: TechnicalFeedbackClassification,
        confidence: TechnicalFeedbackConfidenceLevel,
        importance: TechnicalFeedbackImportance,
        observation: str,
        metric_value: float | None,
        primary_event: StrokePhaseEvent | StrokeAngleMeasurement | None,
        evidence: list[TechnicalFeedbackEvidence],
        phase_references: list[TechnicalFeedbackPhaseReference],
        stroke: StrokeAnalysisSummary,
        limitations: list[str],
        supporting_details: dict[str, float | str | bool],
        rule_id_override: str | None = None,
        comparison_override: str | None = None,
        confidence_source: str = "native",
        confidence_ceiling: TechnicalFeedbackConfidenceLevel | None = None,
        dependency_confidence: TechnicalFeedbackConfidenceLevel | None = None,
        camera_angle: str | None = None,
    ) -> TechnicalFeedbackFinding:
        cleaned_evidence = [item for item in evidence if item is not None]
        cleaned_phases = [item for item in phase_references if item is not None]
        return TechnicalFeedbackFinding(
            rule_id=rule_id_override or rule.id,
            family=rule.family,
            title=title,
            kind=kind,
            classification=classification,
            confidence_level=confidence,
            importance=importance,
            observation=observation,
            metric_name=rule.metric,
            measured_value=metric_value,
            threshold_value=rule.provisional_threshold,
            unit=rule.unit,
            comparison=comparison_override or rule.comparison,
            primary_frame_index=primary_event.frame_index if primary_event is not None else None,
            primary_timestamp_ms=primary_event.timestamp_ms if primary_event is not None else None,
            evidence=cleaned_evidence,
            phase_references=cleaned_phases,
            limitations=limitations,
            supporting_details=supporting_details,
            confidence_source=confidence_source,
            confidence_ceiling=confidence_ceiling,
            dependency_confidence=dependency_confidence,
            camera_angle=camera_angle,
        )

    def _title_for_rule(self, rule_id: str) -> str:
        return {
            "late_preparation": "הכנה מתחילה מאוחר",
            "limited_shoulder_rotation": "סיבוב כתפיים מוגבל",
            "limited_follow_through_rotation": "סיבוב הכתפיים בהמשך קטן מדי",
            "limited_hip_rotation": "סיבוב ירכיים מוגבל",
            "shoulder_hip_sequence_difference": "הפרש בין סיבוב הכתפיים והירכיים",
            "limited_knee_bend": "כיפוף ברכיים מוגבל",
            "arm_too_folded_at_contact": "הזרוע כפופה מדי במגע",
            "arm_near_full_extension_at_contact": "הזרוע כמעט ישרה לגמרי במגע",
            "unstable_finish": "הסיום נראה לא יציב",
            "slow_recovery": "ההתאוששות איטית",
        }.get(rule_id, rule_id)

    def _capability_for_rule(self, capabilities: StrokeAnalysisCapabilitySummary, rule_id: str) -> StrokeCapabilityAssessment | None:
        if rule_id == "late_preparation":
            return capabilities.stroke_phases
        if rule_id in {"limited_shoulder_rotation", "limited_follow_through_rotation"}:
            return capabilities.shoulder_rotation
        if rule_id in {"limited_hip_rotation", "shoulder_hip_sequence_difference"}:
            return capabilities.hip_rotation
        if rule_id in {"arm_too_folded_at_contact", "arm_near_full_extension_at_contact"}:
            return capabilities.elbow_angles
        if rule_id == "unstable_finish":
            return capabilities.balance
        if rule_id == "slow_recovery":
            return capabilities.recovery
        return None

    def _importance_from_confidence(self, confidence: TechnicalFeedbackConfidenceLevel) -> TechnicalFeedbackImportance:
        return "secondary" if confidence in {"low", "insufficient"} else "primary"

    def _classification_from_confidence(self, confidence: TechnicalFeedbackConfidenceLevel) -> TechnicalFeedbackClassification:
        return "possible_observation" if confidence == "low" else "confirmed_observation"

    def _phase_ready(self, phases: StrokePhasesSummary, required: list[str]) -> bool:
        return all(getattr(phases, name, None) is not None for name in required)

    def _phase_event_at_preparation(self, phases: StrokePhasesSummary) -> StrokePhaseEvent | None:
        return phases.preparation_start

    def _phase_event_at_contact(self, phases: StrokePhasesSummary) -> StrokePhaseEvent | None:
        return phases.contact_estimate

    def _phase_ref(self, phase: str, event: StrokePhaseEvent | StrokeAngleMeasurement | None) -> TechnicalFeedbackPhaseReference:
        if event is None:
            return TechnicalFeedbackPhaseReference(phase=phase)
        return TechnicalFeedbackPhaseReference(phase=phase, frame_index=event.frame_index, timestamp_ms=event.timestamp_ms)

    def _evidence(self, label: str, event: StrokePhaseEvent | StrokeAngleMeasurement | None, *, value: float | str | bool | None) -> TechnicalFeedbackEvidence:
        if event is None:
            return TechnicalFeedbackEvidence(label=label, value=value)
        return TechnicalFeedbackEvidence(label=label, frame_index=event.frame_index, timestamp_ms=event.timestamp_ms, value=value)

    def _limitations(
        self,
        confidence: TechnicalFeedbackConfidenceLevel,
        capability: StrokeCapabilityAssessment,
        phase_confidence: StrokeConfidenceLevel,
        pose: PoseAnalysisSummary | None,
    ) -> list[str]:
        notes: list[str] = []
        if confidence == "low":
            notes.append("המדידה מסומנת כצפייה אפשרית בלבד.")
        if confidence == "insufficient":
            notes.append("אין מספיק מידע למדידה יציבה.")
        if phase_confidence == "low":
            notes.append("בטחון שלבי החבטה נמוך.")
        if pose is not None and pose.confidence_level in {"low", "insufficient"}:
            notes.append(f"confidence pose: {pose.confidence_level}")
        if capability.confidence_source == "legacy_derived":
            notes.append("יכולת נגזרה מנתון היסטורי שמור.")
        if capability.diagnostics:
            visible = [diag.usable_frame_percentage for diag in capability.diagnostics if diag.usable_frame_percentage is not None]
            if visible:
                notes.append(f"usable frame share median: {median(visible):.2f}")
        return self._dedupe_strings(notes)

    def _build_confidence(
        self,
        capability: StrokeCapabilityAssessment,
        phase_confidence: StrokeConfidenceLevel,
        pose: PoseAnalysisSummary | None,
        *,
        distance: float,
        continuity: float | None = None,
    ) -> TechnicalFeedbackConfidenceLevel:
        capability_score = self._confidence_score(capability.confidence_level)
        phase_score = self._confidence_score(phase_confidence)
        pose_score = self._confidence_score(pose.confidence_level if pose is not None else "insufficient")
        continuity_score = continuity if continuity is not None else self._continuity_score(capability)
        if continuity_score is None:
            continuity_score = pose.pose_coverage if pose is not None else 0.0
        score = (capability_score * 0.3) + (phase_score * 0.2) + (pose_score * 0.15) + (float(continuity_score) * 0.15) + (min(1.0, max(0.0, distance)) * 0.2)
        if score >= 0.8:
            return "high"
        if score >= 0.6:
            return "medium"
        if score >= 0.38:
            return "low"
        return "insufficient"

    def _confidence_score(self, confidence: StrokeConfidenceLevel | TechnicalFeedbackConfidenceLevel) -> float:
        return {"high": 1.0, "medium": 0.75, "low": 0.45, "insufficient": 0.0}.get(confidence, 0.0)

    def _lowest_confidence(
        self,
        first: StrokeConfidenceLevel | TechnicalFeedbackConfidenceLevel,
        second: StrokeConfidenceLevel | TechnicalFeedbackConfidenceLevel,
    ) -> StrokeConfidenceLevel | TechnicalFeedbackConfidenceLevel:
        return first if self._confidence_rank(first) <= self._confidence_rank(second) else second

    def _confidence_rank(self, confidence: StrokeConfidenceLevel | TechnicalFeedbackConfidenceLevel) -> int:
        return {"insufficient": 0, "low": 1, "medium": 2, "high": 3}.get(confidence, 0)

    def _continuity_score(self, capability: StrokeCapabilityAssessment) -> float | None:
        candidates: list[float] = []
        for diagnostic in capability.diagnostics:
            if diagnostic.usable_frame_percentage is not None:
                candidates.append(float(diagnostic.usable_frame_percentage))
            elif diagnostic.motion_continuity is not None:
                candidates.append(float(diagnostic.motion_continuity))
            elif diagnostic.median_visibility is not None:
                candidates.append(float(diagnostic.median_visibility))
        if not candidates:
            return None
        return max(0.0, min(1.0, float(median(candidates))))

    def _distance_score(self, measured: float, threshold: float, *, invert: bool) -> float:
        if threshold == 0:
            return 0.0
        margin = max(0.0, threshold - measured) if invert else max(0.0, measured - threshold)
        return min(1.0, margin / max(abs(threshold), 1.0))

    def _ratio(self, numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return float(numerator) / float(denominator)

    def _angle_delta(self, previous: StrokeAngleMeasurement | None, current: StrokeAngleMeasurement | None) -> float | None:
        if previous is None or current is None or previous.value_degrees is None or current.value_degrees is None:
            return None
        delta = shortest_angle_delta(previous.value_degrees, current.value_degrees)
        if delta is None:
            return None
        return abs(float(delta))

    def _dedupe_findings(self, findings: list[TechnicalFeedbackFinding]) -> list[TechnicalFeedbackFinding]:
        ordered = sorted(
            findings,
            key=lambda item: (
                0 if item.importance == "primary" else 1,
                {"high": 0, "medium": 1, "low": 2, "insufficient": 3}[item.confidence_level],
                -(float(item.measured_value) if item.measured_value is not None else 0.0),
            ),
        )
        seen: set[str] = set()
        deduped: list[TechnicalFeedbackFinding] = []
        for finding in ordered:
            if finding.rule_id in seen:
                continue
            seen.add(finding.rule_id)
            deduped.append(finding)
        return deduped

    def _limit_findings(self, findings: list[TechnicalFeedbackFinding]) -> list[TechnicalFeedbackFinding]:
        ordered = sorted(
            findings,
            key=lambda item: (
                self._finding_priority(item.rule_id),
                0 if item.importance == "primary" else 1,
                {"high": 0, "medium": 1, "low": 2, "insufficient": 3}[item.confidence_level],
                -(float(item.measured_value) if item.measured_value is not None else 0.0),
            ),
        )
        primary = [finding for finding in ordered if finding.importance == "primary"]
        secondary = [finding for finding in ordered if finding.importance == "secondary"]
        return primary[:3] + secondary[:2]

    def _finding_priority(self, rule_id: str) -> int:
        if rule_id == "late_preparation":
            return 0
        if rule_id in {"arm_too_folded_at_contact", "arm_near_full_extension_at_contact"}:
            return 1
        if rule_id in {"limited_shoulder_rotation", "limited_follow_through_rotation"}:
            return 2
        if rule_id in {"limited_hip_rotation", "shoulder_hip_sequence_difference"}:
            return 3
        if rule_id.startswith("limited_knee_bend"):
            return 4
        if rule_id in {"unstable_finish", "slow_recovery"}:
            return 5
        return 9

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
