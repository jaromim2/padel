from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from ..core.security import require_secret
from ..core.store import JobStore
from ..models import (
    JobArtifacts,
    JobContactFrameUpdateRequest,
    JobCreateRequest,
    JobError,
    JobResponse,
    JobResult,
    JobSelectedPlayerRequest,
    JobStatusUpdateRequest,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _get_store(request: Request) -> JobStore:
    return request.app.state.store


def _row_to_response(row: dict[str, object]) -> JobResponse:
    result = row["result"] or None
    artifacts = JobArtifacts.model_validate(
        {
            "download_url": row["download_url"],
            "downloaded_video_path": row["downloaded_video_path"],
            "downloaded_video_size": row["downloaded_video_size"],
            "downloaded_video_sha256": row["downloaded_video_sha256"],
        }
    )
    return JobResponse(
        job_id=str(row["job_id"]),
        analysis_id=int(row["analysis_id"]),
        status=str(row["status"]),
        workflow_mode=str(row["workflow_mode"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        result=JobResult.model_validate(result) if result else None,
        error=JobError(**row["error"]) if row["error"] else None,
        external_artifacts=artifacts,
        source=row["payload"],
    )


def _resolve_artifact_path(storage_root: Path, storage_key: str) -> Path:
    path = (storage_root / storage_key).resolve()
    root = storage_root.resolve()
    if root not in path.parents and path != root:
        raise ValueError("invalid artifact storage path")
    return path


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(request_body: JobCreateRequest, request: Request, _auth: None = Depends(require_secret)) -> JobResponse:
    store = _get_store(request)
    job = store.create_job(request_body.model_dump(mode="json"))
    return _row_to_response(job)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request, _auth: None = Depends(require_secret)) -> JobResponse:
    store = _get_store(request)
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _row_to_response(job)


@router.delete("/{job_id}")
def delete_job(job_id: str, request: Request, _auth: None = Depends(require_secret)) -> dict[str, bool]:
    store = _get_store(request)
    deleted = store.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return {"success": True}


@router.post("/{job_id}/status", response_model=JobResponse)
def update_job_status(
    job_id: str,
    request_body: JobStatusUpdateRequest,
    request: Request,
    _auth: None = Depends(require_secret),
) -> JobResponse:
    allowed_statuses = {"queued", "processing", "awaiting_player_selection", "completed", "failed"}
    if request_body.status not in allowed_statuses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job status.")

    if request_body.status == "completed" and request_body.result is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Completed jobs require a result.")
    if request_body.status == "failed" and request_body.error is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed jobs require an error.")

    store = _get_store(request)
    job = store.update_job_state(
        job_id,
        request_body.status,
        result=request_body.result.model_dump(mode="json") if request_body.result else None,
        error=request_body.error.model_dump(mode="json") if request_body.error else None,
        external_artifacts=request_body.external_artifacts.model_dump(mode="json") if request_body.external_artifacts else None,
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _row_to_response(job)


@router.post("/{job_id}/selected-player", response_model=JobResponse)
def select_player(
    job_id: str,
    request_body: JobSelectedPlayerRequest,
    request: Request,
    _auth: None = Depends(require_secret),
) -> JobResponse:
    store = _get_store(request)
    try:
        job = store.complete_selected_player_job(
            job_id,
            request_body.selected_player_candidate_id,
            str(request_body.video_download_url) if request_body.video_download_url else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _row_to_response(job)


@router.patch("/{job_id}/contact-frame", response_model=JobResponse)
def update_contact_frame(
    job_id: str,
    request_body: JobContactFrameUpdateRequest,
    request: Request,
    _auth: None = Depends(require_secret),
) -> JobResponse:
    store = _get_store(request)
    try:
        job = store.update_contact_frame(job_id, request_body.frame_index)
    except ValueError as exc:
        if str(exc) == "contact_frame_update_requires_completed_job":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contact frame can only be updated for completed jobs.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return _row_to_response(job)


@router.get("/{job_id}/artifacts/{artifact_name}")
def get_artifact(job_id: str, artifact_name: str, request: Request, _auth: None = Depends(require_secret)):
    store = _get_store(request)
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    result = job.get("result")
    if not isinstance(result, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not available.")

    selected = _resolve_artifact_reference(result, artifact_name)
    if not isinstance(selected, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not available.")

    storage_key = str(selected.get("storage_key", ""))
    if storage_key == "":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not available.")

    try:
        artifact_path = _resolve_artifact_path(store._settings.storage_dir, storage_key)  # noqa: SLF001
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path.")
    if not artifact_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")

    filename = str(selected.get("filename") or artifact_path.name)
    media_type = str(selected.get("mime_type") or "application/octet-stream")
    return FileResponse(artifact_path, media_type=media_type, filename=filename)


@router.post("/lease-next", response_model=JobResponse)
def lease_next_job(request: Request, _auth: None = Depends(require_secret)) -> JobResponse:
    store = _get_store(request)
    job = store.lease_next_job()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No queued jobs available.")
    return _row_to_response(job)


def _resolve_artifact_reference(result: dict[str, object], artifact_name: str) -> dict[str, object] | None:
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        if artifact_name == "annotated-video":
            selected = artifacts.get("annotated_video")
            if isinstance(selected, dict):
                return selected
        if artifact_name == "metadata":
            selected = artifacts.get("metadata")
            if isinstance(selected, dict):
                return selected

    match = result.get("match")
    if not isinstance(match, dict):
        return None

    match_artifacts = match.get("artifacts")
    if isinstance(match_artifacts, dict):
        if artifact_name == "match-preview":
            selected = match_artifacts.get("preview_image")
            if isinstance(selected, dict):
                return selected
        if artifact_name == "tracking-preview":
            selected = match_artifacts.get("tracking_preview_image")
            if isinstance(selected, dict):
                return selected
        if artifact_name == "metadata":
            selected = match_artifacts.get("metadata")
            if isinstance(selected, dict):
                return selected

    preview = match.get("preview")
    if isinstance(preview, dict) and artifact_name.startswith("preview-frame-"):
        try:
            requested_frame = int(artifact_name.removeprefix("preview-frame-"))
        except ValueError:
            requested_frame = -1
        frames = preview.get("frames", [])
        if isinstance(frames, list):
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                if int(frame.get("frame_index", -1)) != requested_frame:
                    continue
                selected = frame.get("artifact")
                if isinstance(selected, dict):
                    return selected

    if not artifact_name.startswith("candidate-"):
        return None

    for candidate in match.get("stroke_candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id", ""))
        if candidate_id and artifact_name in {f"{candidate_id}-clip", f"{candidate_id}-thumbnail"}:
            artifacts = candidate.get("artifacts")
            if isinstance(artifacts, dict):
                selected = artifacts.get("clip" if artifact_name.endswith("-clip") else "thumbnail")
                if isinstance(selected, dict):
                    return selected
    return None
