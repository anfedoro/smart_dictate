from __future__ import annotations

import gc
import importlib
import json
import logging
import sys
import threading
import types
import wave
from pathlib import Path

import numpy as np

from smart_dictate.model_manager import ensure_model
from smart_dictate.paths import models_dir

_MODEL_LOAD_LOCK = threading.Lock()


def warmup_model(model_id: str) -> None:
    model_path = ensure_model(model_id, models_dir())
    _ensure_loaded_model(model_path, word_timestamps=False)


def unload_model(model_id: str) -> bool:
    try:
        mlx_transcribe = importlib.import_module("mlx_whisper.transcribe")
    except ImportError:
        return False
    holder = getattr(mlx_transcribe, "ModelHolder", None)
    if holder is None:
        return False
    expected_path = str(models_dir() / model_id.replace("/", "__"))
    current_path = getattr(holder, "model_path", None)
    current_model = getattr(holder, "model", None)
    if current_model is None or current_path != expected_path:
        return False
    with _MODEL_LOAD_LOCK:
        holder.model = None
        holder.model_path = None
    gc.collect()
    try:
        import mlx.core as mx  # type: ignore
    except Exception:
        mx = None
    if mx is not None:
        try:
            clear_cache = getattr(mx, "clear_cache", None)
            if callable(clear_cache):
                clear_cache()
        except Exception:
            pass
        try:
            metal = getattr(mx, "metal", None)
            clear_metal_cache = getattr(metal, "clear_cache", None) if metal else None
            if callable(clear_metal_cache):
                clear_metal_cache()
        except Exception:
            pass
    gc.collect()
    return True


def _prepare_mlx_whisper_timing_stub(word_timestamps: bool) -> None:
    try:
        import numba  # noqa: F401
        from scipy import signal  # noqa: F401
        return
    except Exception:
        if word_timestamps:
            logging.getLogger(__name__).warning(
                "Word timestamps disabled because numba/scipy are missing."
            )
        if "mlx_whisper.timing" not in sys.modules:
            timing = types.ModuleType("mlx_whisper.timing")

            def add_word_timestamps(**_kwargs):
                return None

            timing.add_word_timestamps = add_word_timestamps
            sys.modules["mlx_whisper.timing"] = timing


def _ensure_loaded_model(model_path: Path, *, word_timestamps: bool) -> bool:
    _prepare_mlx_whisper_timing_stub(word_timestamps)
    try:
        mlx_transcribe = importlib.import_module("mlx_whisper.transcribe")
    except ImportError as exc:
        raise RuntimeError("mlx-whisper is not installed.") from exc
    holder = getattr(mlx_transcribe, "ModelHolder", None)
    if holder is None or not hasattr(holder, "get_model"):
        return False
    model_path_str = str(model_path)
    current_model = getattr(holder, "model", None)
    current_path = getattr(holder, "model_path", None)
    if current_model is not None and current_path == model_path_str:
        return True
    with _MODEL_LOAD_LOCK:
        current_model = getattr(holder, "model", None)
        current_path = getattr(holder, "model_path", None)
        if current_model is None or current_path != model_path_str:
            mx_module = getattr(mlx_transcribe, "mx", None)
            if mx_module is None:
                import mlx.core as mx_module
            holder.get_model(model_path_str, mx_module.float16)
    return True


def transcribe_audio(
    audio_path: Path,
    model_id: str,
    *,
    sample_rate_hz: int = 16000,
    condition_on_previous_text: bool = False,
    language_override: str | None = None,
    segment_on_silence: bool = True,
    min_silence_seconds: float = 0.5,
    min_segment_seconds: float = 1.0,
    max_segment_seconds: float = 0.0,
    segment_padding_seconds: float = 0.15,
    vad_rms_threshold: float = 0.0,
    word_timestamps: bool = False,
    hallucination_silence_threshold: float = 0.0,
) -> str:
    model_path = ensure_model(model_id, models_dir())
    try:
        import mlx_whisper
    except ImportError as exc:
        raise RuntimeError("mlx-whisper is not installed.") from exc
    _ensure_loaded_model(model_path, word_timestamps=word_timestamps)
    if segment_on_silence:
        audio = _load_wav_mono(audio_path, sample_rate_hz)
        if audio is not None:
            segments = _split_on_silence(
                audio,
                sample_rate_hz=sample_rate_hz,
                min_silence_seconds=min_silence_seconds,
                min_segment_seconds=min_segment_seconds,
                max_segment_seconds=max_segment_seconds,
                segment_padding_seconds=segment_padding_seconds,
                vad_rms_threshold=vad_rms_threshold,
            )
            return _transcribe_segments(
                mlx_whisper,
                audio,
                model_path,
                segments,
                condition_on_previous_text,
                language_override,
                word_timestamps,
                hallucination_silence_threshold,
            )
    result = _transcribe_once(
        mlx_whisper,
        str(audio_path),
        model_path,
        condition_on_previous_text,
        language_override,
        word_timestamps,
        hallucination_silence_threshold,
    )
    return _extract_text(result)


def write_transcript_json(
    audio_path: Path,
    text: str,
    *,
    original_text: str | None = None,
    polished_text: str | None = None,
) -> Path:
    payload = {
        "id": audio_path.stem,
        "text": text,
        "original_text": original_text or "",
        "polished_text": polished_text or "",
    }
    json_path = audio_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path


def _extract_text(result) -> str:
    if isinstance(result, dict) and "text" in result:
        return str(result["text"]).strip()
    if isinstance(result, str):
        return result.strip()
    raise RuntimeError("Unexpected transcription result.")


def _load_wav_mono(audio_path: Path, sample_rate_hz: int) -> np.ndarray | None:
    try:
        with wave.open(str(audio_path), "rb") as wf:
            if wf.getnchannels() != 1:
                return None
            if wf.getsampwidth() != 2:
                return None
            if wf.getframerate() != sample_rate_hz:
                return None
            frames = wf.readframes(wf.getnframes())
    except wave.Error:
        return None
    if not frames:
        return None
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _split_on_silence(
    audio: np.ndarray,
    *,
    sample_rate_hz: int,
    min_silence_seconds: float,
    min_segment_seconds: float,
    max_segment_seconds: float,
    segment_padding_seconds: float,
    vad_rms_threshold: float,
) -> list[tuple[int, int]]:
    audio_len = len(audio)
    if audio_len == 0:
        return []
    frame_size = max(1, int(sample_rate_hz * 0.02))
    rms = []
    for idx in range(0, audio_len, frame_size):
        frame = audio[idx : idx + frame_size]
        if frame.size == 0:
            continue
        rms.append(float(np.sqrt(np.mean(frame * frame))))
    if not rms:
        return [(0, audio_len)]
    rms_array = np.array(rms, dtype=np.float32)
    if vad_rms_threshold > 0:
        threshold = vad_rms_threshold
    else:
        noise_floor = float(np.percentile(rms_array, 10))
        threshold = max(noise_floor * 2.5, 0.003)
    silence_frames = rms_array < threshold
    min_silence_frames = max(1, int(min_silence_seconds * sample_rate_hz / frame_size))
    silence_regions: list[tuple[int, int]] = []
    run_start = None
    for idx, silent in enumerate(silence_frames):
        if silent and run_start is None:
            run_start = idx
        elif not silent and run_start is not None:
            if idx - run_start >= min_silence_frames:
                silence_regions.append((run_start, idx))
            run_start = None
    if run_start is not None and len(silence_frames) - run_start >= min_silence_frames:
        silence_regions.append((run_start, len(silence_frames)))
    silence_samples: list[tuple[int, int]] = [
        (start * frame_size, min(end * frame_size, audio_len))
        for start, end in silence_regions
    ]
    min_seg = max(1, int(min_segment_seconds * sample_rate_hz))
    if max_segment_seconds > 0:
        max_seg = max(min_seg, int(max_segment_seconds * sample_rate_hz))
    else:
        max_seg = audio_len
    pad = max(0, int(segment_padding_seconds * sample_rate_hz))
    segments: list[tuple[int, int]] = []
    start = 0
    while start < audio_len:
        target_end = min(start + max_seg, audio_len)
        cut = None
        for s_start, s_end in silence_samples:
            mid = (s_start + s_end) // 2
            if mid <= start:
                continue
            if mid - start < min_seg:
                continue
            if mid > target_end:
                break
            cut = mid
        end = cut if cut is not None else target_end
        if end <= start:
            end = min(start + max_seg, audio_len)
            if end <= start:
                break
        seg_start = max(0, start - pad)
        seg_end = min(audio_len, end + pad)
        segments.append((seg_start, seg_end))
        start = end
    return segments


def _transcribe_segments(
    module,
    audio: np.ndarray,
    model_path: Path,
    segments: list[tuple[int, int]],
    condition_on_previous_text: bool,
    language_override: str | None,
    word_timestamps: bool,
    hallucination_silence_threshold: float,
) -> str:
    texts: list[str] = []
    for start, end in segments:
        chunk = audio[start:end]
        if len(chunk) == 0:
            continue
        result = _transcribe_once(
            module,
            chunk,
            model_path,
            condition_on_previous_text,
            language_override,
            word_timestamps,
            hallucination_silence_threshold,
        )
        text = _extract_text(result)
        if text:
            texts.append(text)
    return " ".join(texts).strip()


def _transcribe_once(
    module,
    audio,
    model_path: Path,
    condition_on_previous_text: bool,
    language_override: str | None,
    word_timestamps: bool,
    hallucination_silence_threshold: float,
):
    kwargs = {
        "path_or_hf_repo": str(model_path),
        "verbose": False,
        "language": language_override,
        "condition_on_previous_text": condition_on_previous_text,
    }
    if word_timestamps:
        kwargs["word_timestamps"] = True
        if hallucination_silence_threshold > 0:
            kwargs["hallucination_silence_threshold"] = hallucination_silence_threshold
    return module.transcribe(audio, **kwargs)
