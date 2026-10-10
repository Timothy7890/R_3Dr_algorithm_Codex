import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from compute_node.config import Settings
from compute_node.processor import (
    default_output_path,
    frames_command,
    map_reconstruction_progress,
    pipeline_command,
    run_reconstruction,
    yolo_ini,
)


class ComputeProcessorTests(unittest.IsolatedAsyncioTestCase):
    def test_output_is_named_after_job_directory(self):
        video = Path("/xxxx/yyyy/A.mp4")
        self.assertEqual(default_output_path(video), Path("/xxxx/yyyy/yyyy_psnppcuda.txt"))

    def test_pipeline_progress_maps_to_processing_range(self):
        self.assertEqual(map_reconstruction_progress(-1), 30)
        self.assertEqual(map_reconstruction_progress(0), 30)
        self.assertEqual(map_reconstruction_progress(50), 60)
        self.assertEqual(map_reconstruction_progress(100), 90)
        self.assertEqual(map_reconstruction_progress(101), 90)

    def test_yolo_ini_selection(self):
        self.assertEqual(yolo_ini(None), "903_YOLO26.ini")
        self.assertEqual(yolo_ini(""), "903_YOLO26.ini")
        self.assertEqual(yolo_ini("lajiao"), "20260720-lajiao.ini")
        self.assertEqual(yolo_ini("yinxing"), "20260323-yinxing.ini")
        self.assertEqual(yolo_ini("tomato"), "20260411-Tomato-006.ini")
        with self.assertRaises(ValueError):
            yolo_ini("apple")

    def test_commands(self):
        settings = Settings(algo_env=Path("/env"), frames_script=Path("/s/get_frames_sharp.py"))
        job = Path("/xxxx/yyyy")
        self.assertEqual(
            frames_command(settings, job),
            ["/env/bin/python", "/s/get_frames_sharp.py", "/xxxx/yyyy", "-L", "1", "-n", "100", "--ext", "png"],
        )
        command = pipeline_command(settings, job, "903_YOLO26.ini")
        self.assertEqual(command[:5], ["/env/bin/psn_pipeline_cuda", "-i", "/xxxx/yyyy", "-L", "1"])
        self.assertIn("--no_show", command)
        self.assertIn("--parallel-sfm-yolo", command)
        self.assertIn("--sfm-orbit-z", command)
        self.assertNotIn("--nerf-viewer", command)
        self.assertEqual(command[command.index("--ini") + 1], "903_YOLO26.ini")

    async def test_run_reconstruction_runs_both_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "task-1"
            job.mkdir()
            video = job / "source.mp4"
            video.write_bytes(b"video")
            output = default_output_path(video)
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                if Path(command[0]).name == "psn_pipeline_cuda":
                    output.write_text("0 0 0 255 0 0\n")
                return subprocess.CompletedProcess(command, 0)

            stages = []
            with patch("compute_node.processor.subprocess.run", side_effect=fake_run):
                await run_reconstruction(video, output, lambda s, p, m: stages.append((s, p)), plant="tomato")

            self.assertEqual(len(calls), 2)
            self.assertEqual(Path(calls[0][1]["cwd"]), job.resolve())
            self.assertIn("20260411-Tomato-006.ini", calls[1][0])
            self.assertTrue(calls[1][1]["env"]["PATH"].startswith(str(Settings().algo_env / "bin")))
            self.assertEqual([s for s, _ in stages], ["extracting_frames", "reconstructing", "completed"])
            self.assertTrue((job / "logs" / "frames.log").is_file())
            self.assertTrue((job / "logs" / "pipeline.log").is_file())

    async def test_failed_step_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "source.mp4"
            video.write_bytes(b"video")
            with patch(
                "compute_node.processor.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1),
            ):
                with self.assertRaisesRegex(RuntimeError, "frames failed"):
                    await run_reconstruction(video, default_output_path(video))

    async def test_missing_output_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "source.mp4"
            video.write_bytes(b"video")
            with patch(
                "compute_node.processor.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            ):
                with self.assertRaises(FileNotFoundError):
                    await run_reconstruction(video, default_output_path(video))


if __name__ == "__main__":
    unittest.main()
