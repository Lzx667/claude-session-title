import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FileLockTest(unittest.TestCase):
    def test_file_lock_is_exclusive(self):
        import file_lock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.lock"
            with path.open("a+b") as first, path.open("a+b") as second:
                file_lock.acquire(first)
                with self.assertRaises(BlockingIOError):
                    file_lock.acquire(second)
                file_lock.release(first)
                file_lock.acquire(second)
                file_lock.release(second)


class PromptTest(unittest.TestCase):
    def test_prompt_has_no_codex_residue(self):
        text = (ROOT / "prompts" / "naming.md").read_text(encoding="utf-8")
        for bad in ("Codex", "codex", "conflicting_titles"):
            self.assertNotIn(bad, text)
        for needed in ("｜", "keep", "rename"):
            self.assertIn(needed, text)


if __name__ == "__main__":
    unittest.main()
