import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "normalize-subtitle-sidecars.py"
SPEC = importlib.util.spec_from_file_location("subtitle_sidecars", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SubtitleSidecarTests(unittest.TestCase):
    def test_preserves_exact_alternate_suffix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Episode.mkv").touch()
            source = root / "Episode.eng.default.srt"
            source.write_text("subtitle", encoding="utf-8")

            changes = MODULE.normalize_directory(root)

            self.assertEqual(changes, [])
            self.assertTrue(source.exists())
            self.assertFalse((root / "Episode.en.srt").exists())

    def test_preserves_exact_anime_hi_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Anime S01E01.mkv").touch()
            source = root / "Anime S01E01.en.hi.srt"
            source.write_text("subtitle", encoding="utf-8")

            changes = MODULE.normalize_directory(root)

            self.assertEqual(changes, [])
            self.assertTrue(source.exists())

    def test_repairs_orphan_in_single_movie_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Clean Movie Name.mkv").touch()
            source = root / "Old.Release.Name.en.srt"
            source.write_text("subtitle", encoding="utf-8")

            MODULE.normalize_directory(root)

            self.assertFalse(source.exists())
            self.assertTrue((root / "Clean Movie Name.en.srt").exists())

    def test_does_not_guess_in_multi_episode_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Show S01E01.mkv").touch()
            (root / "Show S01E02.mkv").touch()
            source = root / "Unknown.en.srt"
            source.write_text("subtitle", encoding="utf-8")

            changes = MODULE.normalize_directory(root)

            self.assertEqual(changes, [])
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
