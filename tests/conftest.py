"""Load this v2 plugin through a nearby MoviePilot test runtime."""

import os
import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = Path(os.environ.get("MOVIEPILOT_BACKEND_PATH", ROOT.parent / "MoviePilot")).expanduser()
if not (BACKEND / "app").is_dir():
    raise RuntimeError("Set MOVIEPILOT_BACKEND_PATH to a MoviePilot checkout")
sys.path.insert(0, str(BACKEND))

import_module("app.testing.bootstrap").prepare_v2_backend(ROOT)
block_real_network = import_module("app.testing.network").block_real_network
