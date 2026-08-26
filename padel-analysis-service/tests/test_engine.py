from __future__ import annotations

from app.core.analysis_engine import MockAnalysisEngine


def test_mock_engine_builds_expected_result() -> None:
    engine = MockAnalysisEngine("0.9.9")
    result = engine.process({"job_id": "job-123", "analysis_id": 42})

    assert result["job_id"] == "job-123"
    assert result["analysis_id"] == 42
    assert result["processor"] == "mock_python_service"
    assert result["service_version"] == "0.9.9"
    assert result["summary"] == "Mock processor completed successfully."
