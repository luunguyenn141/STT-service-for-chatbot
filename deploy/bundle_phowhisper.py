"""Download the pinned PhoWhisper model when building a self-contained image."""

import json
from pathlib import Path
import sys

from huggingface_hub import snapshot_download

model_id = "vinai/PhoWhisper-base"
destination = Path("/opt/models/phowhisper")
revision = sys.argv[1]
snapshot_download(
    repo_id=model_id,
    revision=revision,
    local_dir=destination,
    allow_patterns=["*.json", "*.txt", "*.bin", "*.safetensors"],
)
(destination / "bundle.json").write_text(
    json.dumps({"model_id": model_id, "revision": revision}), encoding="utf-8"
)
