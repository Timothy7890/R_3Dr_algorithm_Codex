import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from compute_node import main as compute
from compute_node.config import Settings
from compute_node.processor import default_output_path


class PlantApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_transfer_fields_reach_real_pipeline_command(self):
        cases = [
            ({"plant_type": "tomato", "yolo_ini": "20260411-Tomato-006.ini"}, "20260411-Tomato-006.ini"),
            ({"plant_type": "eggplant", "yolo_ini": "20261020-eggplant-class.ini"}, "20261020-eggplant-class.ini"),
            ({"plant_type": "pepper", "yolo_ini": "20260720-lajiao.ini"}, "20260720-lajiao.ini"),
            ({"plant_type": "rapeseed", "yolo_ini": "903_YOLO26.ini"}, "903_YOLO26.ini"),
            ({"plant_type": "tomato_new", "yolo_ini": "20261020-Tomato-CEN-fewshot.ini"}, "20261020-Tomato-CEN-fewshot.ini"),
            ({"plant_type": "other", "yolo_ini": "903_YOLO26.ini"}, "903_YOLO26.ini"),
            ({"plant": "lajiao"}, "20260720-lajiao.ini"),
            ({"plant": "yinxing"}, "20260323-yinxing.ini"),
            ({"plant": "lajiao", "plant_type": "pepper"}, "20260720-lajiao.ini"),
            ({}, "903_YOLO26.ini"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, storage_root=Path(tmp), internal_api_key="test-key")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if Path(command[0]).name == "psn_pipeline_cuda":
                    default_output_path(Path(kwargs["cwd"]) / "video.mp4").write_text("0 0 0 255 0 0\n")
                return subprocess.CompletedProcess(command, 0)

            compute.active_jobs = 0
            compute.running_tasks.clear()
            compute.jobs_lock = asyncio.Lock()
            compute.app.state.client = object()
            with patch.object(compute, "settings", settings), patch.object(compute, "post_progress", new=AsyncMock()), patch.object(compute, "post_result", new=AsyncMock()), patch("compute_node.processor.get_settings", return_value=settings), patch("compute_node.processor.subprocess.run", side_effect=fake_run):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=compute.app), base_url="http://compute.test") as client:
                    for index, (fields, expected_ini) in enumerate(cases):
                        with self.subTest(fields=fields):
                            task_id = "plant-test-" + str(index)
                            start = len(commands)
                            response = await client.post(
                                "/internal/jobs/" + task_id,
                                headers={"X-Internal-Key": "test-key"},
                                data={"callback_url": "http://transfer.test/result", "progress_url": "http://transfer.test/progress", **fields},
                                files={"video": ("video.mp4", b"video", "video/mp4")},
                            )
                            self.assertEqual(response.status_code, 200, response.text)
                            for _ in range(100):
                                if task_id not in compute.running_tasks:
                                    break
                                await asyncio.sleep(0.01)
                            self.assertNotIn(task_id, compute.running_tasks)
                            self.assertTrue((Path(tmp) / task_id / ".complete").is_file())
                            self.assertEqual(len(commands) - start, 2)
                            pipeline = commands[-1]
                            self.assertEqual(pipeline[pipeline.index("--ini") + 1], expected_ini)
                            self.assertFalse(Path(expected_ini).is_absolute())
                            reconstruction_only = fields.get("plant_type") == "other"
                            self.assertEqual("--rgb-only" in pipeline, reconstruction_only)
                            self.assertEqual(pipeline[pipeline.index("--nerf-stage2-steps") + 1], "0" if reconstruction_only else "4000")
                            self.assertEqual("yolo" in pipeline, reconstruction_only)
                            self.assertEqual(pipeline[pipeline.index("--sfm-aabb-scale") + 1], "4")
                            self.assertEqual("--sfm-camera-aabb" in pipeline, reconstruction_only)
                            self.assertEqual("--sfm-normalize-camera-scale" in pipeline, reconstruction_only)
                            self.assertEqual("--nerf-export-subject-crop" in pipeline, reconstruction_only)

    async def test_bad_selection_is_rejected_before_algorithm_runs(self):
        cases = [
            {"plant_type": "apple"},
            {"plant": "apple", "plant_type": "tomato"},
            {"plant": "tomato", "plant_type": "pepper"},
            {"plant_type": "tomato", "yolo_ini": "20260720-lajiao.ini"},
            {"plant_type": "eggplant", "yolo_ini": "../../custom.ini"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, storage_root=Path(tmp), internal_api_key="test-key")
            with patch.object(compute, "settings", settings), patch.object(compute, "save_upload", new=AsyncMock()) as save:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=compute.app), base_url="http://compute.test") as client:
                    for fields in cases:
                        response = await client.post(
                            "/internal/jobs/rejected",
                            headers={"X-Internal-Key": "test-key"},
                            data={"callback_url": "http://transfer.test/result", "progress_url": "http://transfer.test/progress", **fields},
                            files={"video": ("video.mp4", b"video")},
                        )
                        self.assertEqual(response.status_code, 422, response.text)
                save.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
