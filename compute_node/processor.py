from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .config import Settings, get_settings


ProgressCallback = Callable[[str, float, str], None]
PROCESSING_PROGRESS_MIN = 30
# 90 is reserved for the PACKAGING(result packaging) transition.
PROCESSING_PROGRESS_MAX = 90

DEFAULT_YOLO_INI = "903_YOLO26.ini"
# psn_pipeline_cuda resolves --ini by name; a full path breaks it.
PLANT_YOLO_INI = {
    "lajiao": "20260720-lajiao.ini",  # Legacy API name.
    "pepper": "20260720-lajiao.ini",
    "eggplant": "20261020-eggplant-class.ini",
    "yinxing": "20260323-yinxing.ini",
    "tomato": "20260411-Tomato-006.ini",
    "rapeseed": "903_YOLO26.ini",
    "tomato_new": "20261020-Tomato-CEN-fewshot.ini",
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


# Algorithm progress (0-100) covered by each stage, in pipeline order.
STAGE_RANGES = {
    "extracting_frames": (0, 15),
    "sfm_and_segmentation": (15, 40),
    "nerf_stage1": (40, 70),
    "nerf_stage2": (70, 95),
    "exporting": (95, 100),
}
STAGE_ORDER = list(STAGE_RANGES)

_TRAIN_BARS = (
    (re.compile(r"Stage1-RGB\+Sem:.*?\|\s*(\d+)/(\d+)"), "nerf_stage1"),
    (re.compile(r"Stage2-Instance:.*?\|\s*(\d+)/(\d+)"), "nerf_stage2"),
)


def parse_pipeline_line(line: str) -> Optional[Tuple[str, float]]:
    """Map one psn_pipeline_cuda log line to (stage, fraction of that stage done)."""
    for pattern, stage in _TRAIN_BARS:
        match = pattern.search(line)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            if stage == "nerf_stage2" and total and done >= total:
                return "exporting", 0.0
            return stage, done / total if total else 0.0
    if "Auto-exporting point cloud" in line or line.startswith("[export]"):
        return "exporting", 0.0
    return None


def stage_progress(stage: str, fraction: float) -> float:
    start, end = STAGE_RANGES[stage]
    return start + (end - start) * max(0.0, min(1.0, fraction))


class _LogWatcher(threading.Thread):
    """Follow the pipeline log while it runs and report stage changes and training steps."""

    def __init__(self, path: Path, report: Callable[[str, float], None], interval: float = 1.0):
        super().__init__(daemon=True)
        self.path = path
        self.report = report
        self.interval = interval
        self.stopped = threading.Event()
        self.stage_index = STAGE_ORDER.index("sfm_and_segmentation")

    def run(self) -> None:
        while not self.path.exists() and not self.stopped.wait(self.interval):
            pass
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8", errors="replace", newline="") as log:
            pending = ""
            while True:
                stopping = self.stopped.wait(self.interval)
                pending += log.read()
                *lines, pending = re.split(r"[\r\n]", pending)
                self.consume(lines)
                if stopping:
                    return

    def consume(self, lines: List[str]) -> None:
        latest: Optional[Tuple[str, float]] = None
        for line in lines:
            parsed = parse_pipeline_line(line)
            if parsed and STAGE_ORDER.index(parsed[0]) >= self.stage_index:
                self.stage_index = STAGE_ORDER.index(parsed[0])
                latest = parsed
        if latest:
            self.report(latest[0], stage_progress(*latest))


def _run_step(
    name: str,
    command: List[str],
    job_dir: Path,
    env: dict,
    on_stage: Optional[Callable[[str, float], None]] = None,
) -> None:
    log_dir = job_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{name}.log"
    watcher = _LogWatcher(log_path, on_stage) if on_stage else None
    with log_path.open("w", encoding="utf-8") as log:
        log.write(" ".join(command) + "\n\n")
        log.flush()
        if watcher:
            watcher.start()
        try:
            code = subprocess.run(command, cwd=job_dir, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        finally:
            if watcher:
                watcher.stopped.set()
                watcher.join(timeout=5)
    if code != 0:
        raise RuntimeError(f"{name} failed ({code}), see {log_path}")


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
    # Without this, Python stages print in large blocks and stage markers reach the log late.
    env["PYTHONUNBUFFERED"] = "1"

    report("extracting_frames", stage_progress("extracting_frames", 0), "extracting frames")
    _run_step("frames", frames_command(settings, job_dir), job_dir, env)

    report("sfm_and_segmentation", stage_progress("sfm_and_segmentation", 0), f"psn_pipeline_cuda --ini {ini}")
    _run_step(
        "pipeline",
        pipeline_command(settings, job_dir, ini),
        job_dir,
        env,
        on_stage=lambda stage, progress: report(stage, progress, stage),
    )

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
