import unittest

from common.filenames import safe_filename


class SafeFilenameTests(unittest.TestCase):
    def test_preserves_unicode_video_filename(self):
        self.assertEqual(safe_filename("室内扫描 01.mp4"), "室内扫描 01.mp4")

    def test_removes_path_components_and_unsafe_characters(self):
        self.assertEqual(safe_filename("..\\uploads\\room?:01.mp4"), "room__01.mp4")

    def test_uses_fallback_for_missing_filename(self):
        self.assertEqual(safe_filename(None), "video.bin")

    def test_avoids_internal_marker_names(self):
        self.assertEqual(safe_filename(".complete"), "complete")


if __name__ == "__main__":
    unittest.main()
