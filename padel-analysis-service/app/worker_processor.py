from __future__ import annotations

from typing import Protocol

from .models import JobResponse, JobResult
from .analyzer_adapter import AnalyzerAdapter, MockAnalyzerAdapter, VideoAnalysisAdapter
from .match_adapter import MatchAnalysisAdapter


class WorkerProcessor(Protocol):
    def build_result(self, job: JobResponse, video_path: str) -> JobResult:
        ...


class MockWorkerProcessor:
    def __init__(self, adapter: AnalyzerAdapter | None = None) -> None:
        self._adapter = adapter or MockAnalyzerAdapter()

    def build_result(self, job: JobResponse, video_path: str) -> JobResult:
        return self._adapter.analyze(job, video_path)


class RealWorkerProcessor:
    def __init__(self, adapter: AnalyzerAdapter | None = None) -> None:
        self._adapter = adapter or VideoAnalysisAdapter()
        self._match_adapter = MatchAnalysisAdapter()

    def build_result(self, job: JobResponse, video_path: str) -> JobResult:
        analysis_mode = str((job.source or {}).get("analysis_mode", "stroke")).strip().lower()
        if analysis_mode == "match":
            return self._match_adapter.analyze(job, video_path)
        return self._adapter.analyze(job, video_path)
