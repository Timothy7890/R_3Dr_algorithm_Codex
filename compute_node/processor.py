from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

from .config import Settings, get_settings


ProgressCallback = Callable[[str, float, str], None]
PROCESSING_PROGRESS_MIN = 30
# 90 is reserved for the PACKAGING(result packaging) transition.
PROCESSING_PROGRESS_MAX = 90

DEFAULT_YOLO_INI = "903_YOLO26.ini"
# psn_pipeline_cuda resolves --ini by name; a full path breaks it.
PLANT_YOLO_INI = {
    "lajiao": "20260720-lajiao.ini",
    "yinxing": "20260323-yinxing.ini",
    "tomato": "20260411-Tomato-006.ini",
}


def map_reconstruction_progress(progress: float) -> int:
    """Map the algorithm's 0-100 progress into the compute-node range."""
    bounded = max(0.0, min(100.0, progress))
    mapped = PROCESSING_PROGRESS_MIN + (
        bounded / 100 * (PROCESSING_PROGRESS_MAX - PROCESSING_PROGRESS_MIN)
    )
    return round(mapped)


def default_output_path(input_path: Path) -> Path:
    """psn_pipeline_cuda writes ``<dir>/<dir name>_psnppcuda.txt``."""
    job_dir = input_path.resolve().parent
    return job_dir / f"{job_dir.name}_psnppcuda.txt"


def yolo_ini(plant: Optional[str]) -> str:
    if not plant:
        return DEFAULT_YOLO_INI
    try:
        return PLANT_YOLO_INI[plant]
    except KeyError:
        raise ValueError(f"unsupported plant: {plant!r}; choose from {', '.join(PLANT_YOLO_INI)}") from None


def frames_command(settings: Settings, job_dir: Path) -> List[str]:
    return [
        str(settings.algo_env / "bin" / "python"),
        str(settings.frames_script),
        str(job_dir),
        "-L", "1",
        "-n", str(settings.frames_count),
        "--ext", "png",
    ]


def pipeline_command(settings: Settings, job_dir: Path, ini: str) -> List[str]:
    return [
        str(settings.algo_env / "bin" / "psn_pipeline_cuda"),
        "-i", str(job_dir),
        "-L", "1",
        "--nerf-stage1-steps", "5000",
        "--nerf-stage2-steps", "4000",
        "--ignore", "reexport", "retrain",
        "--use-legacy-leiden",
        "--nerf-export-save-world-frame",
        "--nerf-no-export-save-nerfstudio-frame",
        "--sfm-aabb-scale", "4",
        "--parallel-sfm-yolo",
        "--sfm-orbit-z",
        "--ini", ini,
        "--no_show",
    ]


def _run_step(name: str, command: List[str], job_dir: Path, env: dict) -> None:
    log_dir = job_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    with (log_dir / f"{name}.log").open("w", encoding="utf-8") as log:
        log.write(" ".join(command) + "\n\n")
        log.flush()
        code = subprocess.run(command, cwd=job_dir, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    if code != 0:
        raise RuntimeError(f"{name} failed ({code}), see {log_dir / f'{name}.log'}")


def _reconstruct(
    settings: Settings,
    input_path: Path,
    output_path: Path,
    ini: str,
    progress_callback: Optional[ProgressCallback],
) -> None:
    def report(stage: str, progress: float, message: str) -> None:
        if progress_callback:
            progress_callback(stage, progress, message)

    job_dir = input_path.resolve().parent
    env = os.environ.copy()
    env["PATH"] = f"{settings.algo_env / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["CONDA_PREFIX"] = str(settings.algo_env)

    report("extracting_frames", 0, "extracting frames")
    _run_step("frames", frames_command(settings, job_dir), job_dir, env)

    report("reconstructing", 10, f"psn_pipeline_cuda --ini {ini}")
    _run_step("pipeline", pipeline_command(settings, job_dir, ini), job_dir, env)

    if not output_path.is_file():
        raise FileNotFoundError(f"pipeline finished but {output_path} was not produced")
    report("completed", 100, f"point cloud written to {output_path}")


async def run_reconstruction(
    input_path: Path,
    output_path: Path,
    progress_callback: Optional[ProgressCallback] = None,
    plant: Optional[str] = None,
) -> None:
    """Extract frames next to the video, then run psn_pipeline_cuda on that directory.

    ``progress_callback`` is invoked from a worker thread.
    """
    ini = yolo_ini(plant)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _reconstruct, get_settings(), input_path, output_path, ini, progress_callback
    )
