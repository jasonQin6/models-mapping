"""Put the skill's scripts directory on the import path for every test module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
