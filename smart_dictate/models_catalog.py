from __future__ import annotations

import os
import shutil
from pathlib import Path

from huggingface_hub import HfApi


FALLBACK_MODELS = [
    "mlx-community/whisper-tiny",
    "mlx-community/whisper-base",
    "mlx-community/whisper-small",
    "mlx-community/whisper-medium",
    "mlx-community/whisper-large-v3-mlx",
]


def fetch_whisper_models() -> list[str]:
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
    api = HfApi(token=token)
    try:
        models = api.list_models(author="mlx-community", search="whisper", limit=200)
    except Exception:
        return FALLBACK_MODELS.copy()
    result = []
    for model in models:
        model_id = getattr(model, "modelId", None)
        if not model_id:
            continue
        if "whisper" not in model_id:
            continue
        result.append(model_id)
    if not result:
        return FALLBACK_MODELS.copy()
    return sorted(set(result))


def list_downloaded_models(models_dir: Path) -> list[str]:
    if not models_dir.exists():
        return []
    result = []
    for entry in models_dir.iterdir():
        if not entry.is_dir():
            continue
        result.append(entry.name.replace("__", "/"))
    return sorted(result)


def is_model_downloaded(models_dir: Path, model_id: str) -> bool:
    target = models_dir / model_id.replace("/", "__")
    return target.exists() and any(target.iterdir())


def delete_model(models_dir: Path, model_id: str) -> None:
    target = models_dir / model_id.replace("/", "__")
    if target.exists():
        shutil.rmtree(target)
