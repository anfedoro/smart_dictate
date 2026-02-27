from __future__ import annotations

from dataclasses import dataclass

import Quartz


@dataclass(frozen=True)
class Settings:
    hotkey_keycode: int | None = None
    hotkey_modifiers: int = Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskSecondaryFn
    hotkey_description: str = "Fn+Ctrl"
    model_id: str = "mlx-community/whisper-small-mlx"
    sample_rate_hz: int = 16000
    channels: int = 1
    condition_on_previous_text: bool = False
    segment_on_silence: bool = True
    min_silence_seconds: float = 0.5
    min_segment_seconds: float = 1.0
    max_segment_seconds: float = 0.0
    segment_padding_seconds: float = 0.15
    vad_rms_threshold: float = 0.0
    word_timestamps: bool = False
    hallucination_silence_threshold: float = 0.0
