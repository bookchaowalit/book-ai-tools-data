import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_ai_tools import JOBS


class RunAIToolsTests(unittest.TestCase):
    def test_job_roster(self):
        self.assertEqual([name for name, _cls in JOBS], ["ai_tools"])


if __name__ == "__main__":
    unittest.main()
