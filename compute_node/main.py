from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Dict, Set, Tuple
from urllib.parse import urlparse
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status

from common.filenames import safe_filename

from .config import Settings, get_settings
from .processor import (
    PROCESSING_PROGRESS_MIN,
    default_output_path,
    map_reconstruction_progress,
    run_reconstruction,
)


settings = get_settings()
logger = logging.getLogger(__name__)
active_jobs = 0
jobs_lock = asyncio.Lock()
running_tasks: Set[str] = set()
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    if x_internal_key != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal key")


def validate_callback_url(value: str, field_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail=f"invalid {field_name}")
    return value


async def save_upload(file: UploadFile, destination: Path) -> Tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    size = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="upload exceeds maximum size")
                digest.update(chunk)
                output.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return size, digest.hexdigest()


async def post_progress(
    client: httpx.AsyncClient,
    progress_url: str,
    task_id: str,
    status_value: str,
    progress: float,
    stage: str,
    compute_node_id: str,
) -> None:
    response = await client.post(
        progress_url,
        json={
            "status": status_value,
            "progress": progress,
            "stage": stage,
            "compute_node_id": compute_node_id,
        },
        headers={"X-Internal-Key": settings.internal_api_key},
    )
    response.raise_for_status()


async def post_result(
    client: httpx.AsyncClient,
    callback_url: str,
    task_id: str,
    output_path: Path,
) -> None:
    print(datetime.now());
    print(f"post_result: task_id:{task_id}, callback_url:{callback_url}");
    with output_path.open("rb") as output:
        response = await client.post(
            callback_url,
            files={"file": (output_path.name, output, "application/octet-stream")},
            headers={"X-Internal-Key": settings.internal_api_key},
            timeout=httpx.Timeout(connect=10, read=300, write=300, pool=10),
        )
    response.raise_for_status()


async def process_job(
    task_id: str,
    input_path: Path,
    callback_url: str,
    progress_url: str,
) -> None:
    global active_jobs
    output_path = default_output_path(input_path)
    client = app.state.client
    try:
        await post_progress(client, progress_url, task_id, "PROCESSING", 30, "processing", settings.node_id)
        event_loop = asyncio.get_running_loop()
        last_reported_progress = PROCESSING_PROGRESS_MIN
        last_reported_stage = "processing"

        def report_reconstruction_progress(stage: str, progress: float, message: str) -> None:
            nonlocal last_reported_progress, last_reported_stage
            mapped_progress = map_reconstruction_progress(progress)
            current_stage = stage
            if mapped_progress < last_reported_progress:
                return
            if mapped_progress == last_reported_progress and current_stage == last_reported_stage:
                return
            last_reported_progress = mapped_progress
            last_reported_stage = current_stage
            logger.info(
                "Reconstruction progress: task_id=%s pipeline_stage=%s pipeline_progress=%s "
                "progress=%s message=%s",
                task_id,
                stage,
                progress,
                mapped_progress,
                message,
            )
            future = asyncio.run_coroutine_threadsafe(
                post_progress(
                    client,
                    progress_url,
                    task_id,
                    "PROCESSING",
                    mapped_progress,
                    current_stage,
                    settings.node_id,
                ),
                event_loop,
            )
            future.result()

        await run_reconstruction(
            input_path=input_path,
            output_path=output_path,
            progress_callback=report_reconstruction_progress,
        )
        await post_progress(client, progress_url, task_id, "PACKAGING", 90, "result packaging", settings.node_id)
        await post_result(client, callback_url, task_id, output_path)
        (input_path.parent / ".complete").touch()
    except Exception as exc:
        logger.exception("Job failed: task_id=%s", task_id)
        with suppress(Exception):
            await post_progress(
                client,
                progress_url,
                task_id,
                "FAILED",
                25,
                "compute_failed",
                settings.node_id,
            )
        logger.error("task_id=%s error=%s", task_id, exc)
    finally:
        async with jobs_lock:
            running_tasks.discard(task_id)
            active_jobs -= 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    app.state.client = httpx.AsyncClient(timeout=settings.callback_timeout_seconds)
    yield
    await app.state.client.aclose()


app = FastAPI(title="3D Reconstruction Compute Node", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> Dict[str, object]:
    return {
        "status": "ok",
        "node_id": settings.node_id,
        "accepting_tasks": active_jobs < settings.capacity,
        "active_jobs": active_jobs,
        "capacity": settings.capacity,
    }


@app.post("/internal/jobs/{task_id}", dependencies=[Depends(require_internal_key)])
async def create_job(
    task_id: str,
    video: UploadFile = File(...),
    callback_url: str = Form(...),
    progress_url: str = Form(...),
) -> Dict[str, object]:
    global active_jobs
    if not TASK_ID_PATTERN.fullmatch(task_id):
        await video.close()
        raise HTTPException(status_code=422, detail="invalid task_id")
    validate_callback_url(callback_url, "callback_url")
    validate_callback_url(progress_url, "progress_url")
    task_dir = settings.storage_root / task_id
    input_path = task_dir / safe_filename(video.filename)
    print(datetime.now());
    print(f"receive_job: task_id:{task_id}, callback_url:{callback_url}");

    async with jobs_lock:
        if task_id in running_tasks or (task_dir / ".complete").exists():
            await video.close()
            return {"task_id": task_id, "accepted": True, "duplicate": True}
        if active_jobs >= settings.capacity:
            await video.close()
            raise HTTPException(status_code=429, detail="compute capacity reached")
        running_tasks.add(task_id)
        active_jobs += 1

    try:
        size, sha256 = await save_upload(video, input_path)
    except Exception:
        async with jobs_lock:
            running_tasks.discard(task_id)
            active_jobs -= 1
        raise

    asyncio.create_task(
        process_job(task_id, input_path, callback_url, progress_url)
    )
    return {
        "task_id": task_id,
        "accepted": True,
        "size": size,
        "sha256": sha256,
    }


@app.get("/")
def root() -> Dict[str, str]:
    return {
        "service": "3D Reconstruction Compute Node",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }

