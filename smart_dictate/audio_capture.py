from __future__ import annotations

from datetime import datetime
from pathlib import Path

from AVFoundation import (
    AVAudioQualityHigh,
    AVAudioRecorder,
    AVEncoderAudioQualityKey,
    AVFormatIDKey,
    AVLinearPCMBitDepthKey,
    AVLinearPCMIsBigEndianKey,
    AVLinearPCMIsFloatKey,
    AVNumberOfChannelsKey,
    AVSampleRateKey,
)
from Foundation import NSURL

K_AUDIO_FORMAT_LINEAR_PCM = int.from_bytes(b"lpcm", "big")


class AudioCapture:
    def __init__(self, output_dir: Path, sample_rate_hz: int, channels: int) -> None:
        self._output_dir = output_dir
        self._sample_rate_hz = sample_rate_hz
        self._channels = channels
        self._active = False
        self._recorder: AVAudioRecorder | None = None
        self._current_path: Path | None = None

    def start(self) -> Path:
        if self._active:
            raise RuntimeError("Recording already active.")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("recording_%Y%m%d_%H%M%S.wav")
        self._current_path = self._output_dir / filename
        url = NSURL.fileURLWithPath_(str(self._current_path))
        settings = {
            AVFormatIDKey: K_AUDIO_FORMAT_LINEAR_PCM,
            AVSampleRateKey: float(self._sample_rate_hz),
            AVNumberOfChannelsKey: int(self._channels),
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsBigEndianKey: False,
            AVLinearPCMIsFloatKey: False,
            AVEncoderAudioQualityKey: AVAudioQualityHigh,
        }
        recorder, error = AVAudioRecorder.alloc().initWithURL_settings_error_(
            url,
            settings,
            None,
        )
        if error is not None:
            raise RuntimeError(f"Failed to create recorder: {error}")
        if recorder is None:
            raise RuntimeError("Failed to create recorder.")
        if not recorder.prepareToRecord():
            raise RuntimeError("Recorder prepareToRecord failed.")
        if not recorder.record():
            raise RuntimeError("Recorder start failed.")
        self._recorder = recorder
        self._active = True
        return self._current_path

    def stop(self) -> Path | None:
        if not self._active or self._recorder is None:
            return self._current_path
        self._recorder.stop()
        self._active = False
        return self._current_path
