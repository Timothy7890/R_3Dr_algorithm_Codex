from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional


ProgressCallback = Callable[[str, float, str], None]
PROCESSING_PROGRESS_MIN = 30
# 90 is reserved for the PACKAGING(result packaging) transition.
PROCESSING_PROGRESS_MAX = 90


def map_reconstruction_progress(progress: float) -> int:
    """Map the algorithm's 0-100 progress into the compute-node range."""
    bounded = max(0.0, min(100.0, progress))
    mapped = PROCESSING_PROGRESS_MIN + (
        bounded / 100 * (PROCESSING_PROGRESS_MAX - PROCESSING_PROGRESS_MIN)
    )
    return round(mapped)


def default_output_path(input_path: Path) -> Path:
    """The result file sent back to the transfer node."""
    return input_path.with_suffix(".txt")


async def run_reconstruction(
    input_path: Path,
    output_path: Path,
    progress_callback: Optional[ProgressCallback] = None,
) -> None:
    """Turn the video at ``input_path`` into a point cloud at ``output_path``.

    ``progress_callback(stage, progress, message)`` may be called from any
    thread; ``progress`` is 0-100. Long blocking work must run off the event
    loop (e.g. ``loop.run_in_executor``) so ``/health`` stays responsive.
    """
    raise NotImplementedError("reconstruction algorithm not implemented")
