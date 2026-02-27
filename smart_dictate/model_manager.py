from __future__ import annotations

import os
import threading
from pathlib import Path

from huggingface_hub import snapshot_download

_MODEL_DOWNLOAD_LOCK = threading.Lock()


def ensure_model(model_id: str, cache_dir: Path) -> Path:
    with _MODEL_DOWNLOAD_LOCK:
        cache_dir.mkdir(parents=True, exist_ok=True)
        target_dir = cache_dir / model_id.replace("/", "__")
        if target_dir.exists() and any(target_dir.iterdir()):
            return target_dir
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
        snapshot_download(
            repo_id=model_id,
            local_dir=str(target_dir),
            token=token,
        )
        return target_dir
