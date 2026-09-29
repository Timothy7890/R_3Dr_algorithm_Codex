"""Run the Nerfstudio video-to-point-cloud pipeline.

The module deliberately invokes Nerfstudio's public CLI commands instead of
depending on private Nerfstudio internals. This keeps the adapter usable
across Nerfstudio releases and makes failures visible in the command logs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


ProgressCallback = Callable[[str, float, str], None]


@dataclass(frozen=True)
class ReconstructionConfig:
    video_path: Path
    output_path: Path
    work_dir: Path | None = None
    max_iterations: int = 30_000
    num_points: int = 1_000_000
    keep_intermediate: bool = True
    nerfstudio_command: str = "ns-process-data"
    nerfstudio_train_command: str = "ns-train"
    nerfstudio_export_command: str = "ns-export"


def _emit(callback: ProgressCallback | None, stage: str, progress: float, message: str) -> None:
    if callback:
        callback(stage, max(0.0, min(100.0, progress)), message)


def _run(command: Sequence[str], cwd: Path, callback: ProgressCallback | None, stage: str) -> None:
    """Run a CLI command while forwarding its output and raising useful errors."""
    printable = " ".join(f'"{part}"' if " " in part else part for part in command)
    _emit(callback, stage, 0, printable)
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if line:
            print(line, flush=True)
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"Nerfstudio command failed ({return_code}): {printable}")


def _find_config(training_dir: Path) -> Path:
    configs = sorted(training_dir.rglob("config.yml"))
    if not configs:
        raise FileNotFoundError(f"Could not find Nerfstudio config.yml under {training_dir}")
    return configs[-1]


def _save_point_cloud(source: Path, output: Path) -> None:
    if output.suffix.lower() == ".ply":
        shutil.copy2(source, output)
        return

    try:
        import open3d as o3d
    except ImportError as exc:
        raise RuntimeError("Open3D is required for .pcd and .txt output") from exc

    cloud = o3d.io.read_point_cloud(str(source))
    if not cloud.has_points():
        raise ValueError(f"Exported point cloud contains no points: {source}")

    temporary = output.with_name(f"{output.stem}.tmp{output.suffix}")
    try:
        if output.suffix.lower() == ".pcd":
            if not o3d.io.write_point_cloud(str(temporary), cloud):
                raise RuntimeError(f"Open3D could not write PCD file: {output}")
        else:
            has_colors = cloud.has_colors()
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for index, point in enumerate(cloud.points):
                    values = list(point)
                    if has_colors:
                        values.extend(cloud.colors[index])
                    stream.write(" ".join(f"{float(value):.9g}" for value in values) + "\n")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def reconstruct_video(config: ReconstructionConfig, callback: ProgressCallback | None = None) -> Path:
    """Convert one video into a PLY, PCD or TXT point cloud."""
    video = config.video_path.expanduser().resolve()
    output = config.output_path.expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Video does not exist: {video}")
    if output.suffix.lower() not in {".ply", ".pcd", ".txt"}:
        raise ValueError("output_path must end with .ply, .pcd or .txt")
    if config.max_iterations <= 0 or config.num_points <= 0:
        raise ValueError("max_iterations and num_points must be positive")

    work = (config.work_dir or output.parent / f"{output.stem}.nerfstudio").expanduser().resolve()
    if not config.keep_intermediate and (output == work or work in output.parents):
        raise ValueError("output_path must be outside work_dir when removing intermediate files")
    data_dir = work / "processed"
    training_dir = work / "training"
    export_dir = work / "pointcloud"
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    _emit(callback, "preparing", 2, "Processing video frames")
    _run(
        [config.nerfstudio_command, "video", "--data", str(video), "--output-dir", str(data_dir)],
        work,
        callback,
        "preparing",
    )

    _emit(callback, "training", 20, "Training Splatfacto model")
    _run(
        [
            config.nerfstudio_train_command,
            "splatfacto",
            "--data",
            str(data_dir),
            "--output-dir",
            str(training_dir),
            "--max-num-iterations",
            str(config.max_iterations),
        ],
        work,
        callback,
        "training",
    )

    model_config = _find_config(training_dir)
    _emit(callback, "exporting", 85, "Exporting point cloud")
    _run(
        [
            config.nerfstudio_export_command,
            "pointcloud",
            "--load-config",
            str(model_config),
            "--output-dir",
            str(export_dir),
            "--num-points",
            str(config.num_points),
            "--remove-outliers",
            "True",
            "--save-world-frame",
            "True",
        ],
        work,
        callback,
        "exporting",
    )
    pointclouds = sorted(export_dir.rglob("*.ply"))
    if not pointclouds:
        raise FileNotFoundError(f"Nerfstudio did not produce a PLY file under {export_dir}")
    _emit(callback, "converting", 95, f"Writing {output.suffix.lower()} point cloud")
    _save_point_cloud(pointclouds[-1], output)
    if not config.keep_intermediate:
        shutil.rmtree(work, ignore_errors=True)
    _emit(callback, "completed", 100, f"Point cloud written to {output}")
    return output


def json_progress(stage: str, progress: float, message: str) -> None:
    print(json.dumps({"stage": stage, "progress": round(progress, 2), "message": message}, ensure_ascii=False), flush=True)


def _env_or_default(name: str, default: str) -> str:
    return os.environ.get(name, default)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate a PLY, PCD or TXT point cloud from a video with Nerfstudio")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Output .ply, .pcd or .txt path")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--max-iterations", type=int, default=30_000)
    parser.add_argument("--num-points", type=int, default=1_000_000)
    # BooleanOptionalAction was added in Python 3.9. Keep equivalent flags
    # explicit so the CLI also works in the Python 3.8 Nerfstudio environment.
    parser.add_argument("--keep-intermediate", dest="keep_intermediate", action="store_true")
    parser.add_argument("--no-keep-intermediate", dest="keep_intermediate", action="store_false")
    parser.set_defaults(keep_intermediate=True)
    args = parser.parse_args()
    config = ReconstructionConfig(
        video_path=args.video,
        output_path=args.output,
        work_dir=args.work_dir,
        max_iterations=args.max_iterations,
        num_points=args.num_points,
        keep_intermediate=args.keep_intermediate,
        nerfstudio_command=_env_or_default("NS_PROCESS_DATA", "ns-process-data"),
        nerfstudio_train_command=_env_or_default("NS_TRAIN", "ns-train"),
        nerfstudio_export_command=_env_or_default("NS_EXPORT", "ns-export"),
    )
    try:
        reconstruct_video(config, json_progress)
    except Exception as exc:
        print(json.dumps({"stage": "failed", "progress": 0, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
