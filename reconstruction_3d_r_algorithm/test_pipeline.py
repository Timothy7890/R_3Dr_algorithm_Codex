import io
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from reconstruction_3d_r_algorithm.pipeline import (
    ReconstructionConfig,
    _save_point_cloud,
    reconstruct_video,
)


class PipelineTests(unittest.TestCase):
    def test_runs_nerfstudio_commands_and_copies_ply(self):
        root = Path(__file__).parent
        output = root / "result.ply"
        commands = []

        def configs(path, _pattern):
            if path.name == "training":
                return [path / "run" / "config.yml"]
            if path.name == "pointcloud":
                return [path / "point_cloud.ply"]
            return []

        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "is_file", return_value=True))
            stack.enter_context(patch.object(Path, "mkdir"))
            stack.enter_context(patch.object(Path, "rglob", side_effect=configs, autospec=True))
            stack.enter_context(patch(
                "reconstruction_3d_r_algorithm.pipeline._run",
                side_effect=lambda command, *_: commands.append(list(command)),
            ))
            copy = stack.enter_context(patch("reconstruction_3d_r_algorithm.pipeline.shutil.copy2"))
            actual = reconstruct_video(ReconstructionConfig(root / "input.mp4", output, max_iterations=100, num_points=10))

        self.assertEqual(actual, output.resolve())
        self.assertEqual(copy.call_args.args[1], output.resolve())
        self.assertEqual([command[:2] for command in commands], [
            ["ns-process-data", "video"], ["ns-train", "splatfacto"], ["ns-export", "pointcloud"]
        ])
        self.assertIn("--load-config", commands[2])

    def test_rejects_output_inside_deleted_work_dir(self):
        root = Path(__file__).parent
        work = root / "work"
        with patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(ValueError, "outside work_dir"):
                reconstruct_video(ReconstructionConfig(root / "input.mp4", work / "result.ply", work_dir=work, keep_intermediate=False))

    def test_rejects_unsupported_extension(self):
        with patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(ValueError, "must end with"):
                reconstruct_video(ReconstructionConfig(Path("input.mp4"), Path("result.xyz")))

    def test_txt_contains_coordinates_and_colors(self):
        cloud = types.SimpleNamespace(
            has_points=lambda: True,
            has_colors=lambda: True,
            points=[(1, 2, 3), (4, 5, 6)],
            colors=[(0.2, 0.4, 0.6), (0.7, 0.8, 0.9)],
        )
        fake_open3d = types.SimpleNamespace(io=types.SimpleNamespace(read_point_cloud=lambda _: cloud))
        class CapturedText(io.StringIO):
            def close(self):
                pass

        stream = CapturedText()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"open3d": fake_open3d}))
            stack.enter_context(patch.object(Path, "open", return_value=stream))
            replace = stack.enter_context(patch.object(Path, "replace"))
            stack.enter_context(patch.object(Path, "unlink"))
            _save_point_cloud(Path("source.ply"), Path("result.txt"))

        self.assertEqual(stream.getvalue(), "1 2 3 0.2 0.4 0.6\n4 5 6 0.7 0.8 0.9\n")
        replace.assert_called_once()

    def test_pcd_uses_open3d_writer(self):
        cloud = types.SimpleNamespace(has_points=lambda: True)
        writes = []
        fake_open3d = types.SimpleNamespace(io=types.SimpleNamespace(
            read_point_cloud=lambda _: cloud,
            write_point_cloud=lambda path, value: writes.append((path, value)) or True,
        ))
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"open3d": fake_open3d}))
            replace = stack.enter_context(patch.object(Path, "replace"))
            stack.enter_context(patch.object(Path, "unlink"))
            _save_point_cloud(Path("source.ply"), Path("result.pcd"))

        self.assertEqual(writes, [("result.tmp.pcd", cloud)])
        replace.assert_called_once()


if __name__ == "__main__":
    unittest.main()
