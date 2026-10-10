import unittest
from pathlib import Path

from compute_node.processor import (
    default_output_path,
    map_reconstruction_progress,
    run_reconstruction,
)


class ComputeProcessorTests(unittest.IsolatedAsyncioTestCase):
    def test_default_output_uses_video_name_and_txt_extension(self):
        video = Path("jobs/task-1/室内扫描 01.mp4")
        self.assertEqual(default_output_path(video), Path("jobs/task-1/室内扫描 01.txt"))

    def test_pipeline_progress_maps_to_processing_range(self):
        self.assertEqual(map_reconstruction_progress(-1), 30)
        self.assertEqual(map_reconstruction_progress(0), 30)
        self.assertEqual(map_reconstruction_progress(50), 60)
        self.assertEqual(map_reconstruction_progress(100), 90)
        self.assertEqual(map_reconstruction_progress(101), 90)

    async def test_reconstruction_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            await run_reconstruction(Path("input.mp4"), Path("input.txt"))


if __name__ == "__main__":
    unittest.main()
