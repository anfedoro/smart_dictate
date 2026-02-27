from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AppConfig:
    language: str | None = None
    model_id: str | None = None
    model_idle_minutes: int | None = None
    app_hash: str | None = None
    postprocess_enabled: bool = False
    postprocess_base_url: str | None = None
    postprocess_model: str | None = None
    postprocess_system_prompt: str | None = None
    hotkey_modifiers: int | None = None
    hotkey_keycode: int | None = None
    hotkey_label: str | None = None


def _toml_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
        .replace('"', '\\"')
    )
    return f"\"{escaped}\""


def load_config(path: Path) -> AppConfig:
    def _read_toml(file_path: Path) -> dict | None:
        try:
            return tomllib.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    if not path.exists():
        return AppConfig()
    data = _read_toml(path)
    if data is None:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if backup_path.exists():
            data = _read_toml(backup_path)
        if data is None:
            return AppConfig()
    transcription = data.get("transcription", {})
    app = data.get("app", {})
    postprocess = data.get("postprocess", {})
    hotkey = data.get("hotkey", {})
    language = transcription.get("language")
    model_id = transcription.get("model_id")
    idle_minutes = transcription.get("model_idle_minutes")
    app_hash = app.get("hash")
    postprocess_enabled = postprocess.get("enabled", False)
    postprocess_base_url = postprocess.get("base_url")
    postprocess_model = postprocess.get("model")
    postprocess_system_prompt = postprocess.get("system_prompt")
    if not language or str(language).lower() == "auto":
        language = None
    else:
        language = str(language)
    if not model_id or str(model_id).lower() == "default":
        model_id = None
    else:
        model_id = str(model_id)
    if idle_minutes is None or str(idle_minutes).lower() in {"", "default"}:
        idle_minutes = None
    else:
        try:
            idle_minutes = int(idle_minutes)
        except (TypeError, ValueError):
            idle_minutes = None
        else:
            if idle_minutes < 0:
                idle_minutes = None
    modifiers = hotkey.get("modifiers")
    keycode = hotkey.get("keycode")
    label = hotkey.get("label")
    try:
        modifiers = int(modifiers) if modifiers is not None else None
    except (TypeError, ValueError):
        modifiers = None
    try:
        keycode = int(keycode) if keycode is not None else None
    except (TypeError, ValueError):
        keycode = None
    if isinstance(label, str) and label.strip():
        label = label.strip()
    else:
        label = None
    if isinstance(postprocess_enabled, str):
        postprocess_enabled = postprocess_enabled.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    else:
        postprocess_enabled = bool(postprocess_enabled)
    if isinstance(postprocess_base_url, str) and postprocess_base_url.strip():
        postprocess_base_url = postprocess_base_url.strip()
    else:
        postprocess_base_url = None
    if isinstance(postprocess_model, str) and postprocess_model.strip():
        postprocess_model = postprocess_model.strip()
    else:
        postprocess_model = None
    if isinstance(postprocess_system_prompt, str) and postprocess_system_prompt.strip():
        postprocess_system_prompt = postprocess_system_prompt.strip()
    else:
        postprocess_system_prompt = None
    return AppConfig(
        language=language,
        model_id=model_id,
        model_idle_minutes=idle_minutes,
        app_hash=str(app_hash) if app_hash else None,
        postprocess_enabled=postprocess_enabled,
        postprocess_base_url=postprocess_base_url,
        postprocess_model=postprocess_model,
        postprocess_system_prompt=postprocess_system_prompt,
        hotkey_modifiers=modifiers,
        hotkey_keycode=keycode,
        hotkey_label=label,
    )


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    language = config.language if config.language else "auto"
    model_id = config.model_id if config.model_id else "default"
    idle_minutes = (
        "default"
        if config.model_idle_minutes is None
        else str(config.model_idle_minutes)
    )
    app_hash = config.app_hash or ""
    postprocess_enabled = "true" if config.postprocess_enabled else "false"
    postprocess_base_url = config.postprocess_base_url or ""
    postprocess_model = config.postprocess_model or ""
    postprocess_system_prompt = config.postprocess_system_prompt or ""
    modifiers = "" if config.hotkey_modifiers is None else str(config.hotkey_modifiers)
    keycode = "" if config.hotkey_keycode is None else str(config.hotkey_keycode)
    label = config.hotkey_label or ""
    content = (
        "[app]\n"
        f"hash = {_toml_quote(app_hash)}\n"
        "\n"
        "[postprocess]\n"
        f"enabled = {_toml_quote(postprocess_enabled)}\n"
        f"base_url = {_toml_quote(postprocess_base_url)}\n"
        f"model = {_toml_quote(postprocess_model)}\n"
        f"system_prompt = {_toml_quote(postprocess_system_prompt)}\n"
        "\n"
        "[transcription]\n"
        f"language = {_toml_quote(language)}\n"
        f"model_id = {_toml_quote(model_id)}\n"
        f"model_idle_minutes = {_toml_quote(idle_minutes)}\n"
        "\n"
        "[hotkey]\n"
        f"modifiers = {_toml_quote(modifiers)}\n"
        f"keycode = {_toml_quote(keycode)}\n"
        f"label = {_toml_quote(label)}\n"
    )
    if path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        try:
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    path.write_text(content, encoding="utf-8")
