import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_ai_tools import JOBS, run_ai_tools  # noqa: E402


class RunAIToolsTests(unittest.TestCase):
    def test_job_roster(self):
        self.assertEqual([name for name, _cls in JOBS], ["ai_tools"])

    def test_dry_run_does_not_collect(self):
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(run_ai_tools(Path(directory), dry_run=True))
        self.assertEqual(result[0]["status"], "dry-run")
        self.assertEqual(result[0]["network"], "not-used")


if __name__ == "__main__":
    unittest.main()
