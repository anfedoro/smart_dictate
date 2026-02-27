# Smart Dictate

Local macOS dictation tool using MLX Whisper. Lightweight AppKit UI, global hotkeys, local transcription.

## MVP scope (planned)
- Global hotkey to toggle recording
- Minimal micro UI with a mic activity animation
- Record mic audio to a local file
- Transcribe with local Whisper MLX
- Post-processing with another model is out of scope for now
 - Transcripts saved to `~/SmartDictate`

## Planned module layout
- `smart_dictate/cli.py` - CLI entrypoint for `uv tool install`
- `smart_dictate/app.py` - AppKit UI + event loop
- `smart_dictate/hotkeys.py` - global hotkeys (Quartz/Carbon)
- `smart_dictate/audio_capture.py` - mic recording via AVFoundation
- `smart_dictate/transcription.py` - Whisper MLX transcription pipeline
- `smart_dictate/model_manager.py` - model download and cache
- `smart_dictate/settings.py` - defaults (hotkeys, model id)
- `smart_dictate/paths.py` - cache/data locations
- `smart_dictate/logging_setup.py` - logging setup

## Dependencies (planned)
- `pyobjc-framework-Cocoa`
- `pyobjc-framework-Quartz`
- `pyobjc-framework-AVFoundation`
- `mlx`
- `mlx-whisper`
- `huggingface-hub`

## Install (uv)
Local install for development:

```bash
uv tool install .
```

Install from git repo (replace URL):

```bash
uv tool install git+https://github.com/your-user/smart_dictate.git
```

## Usage

```bash
smart-dictate
```

## Build macOS .app (PyInstaller)

```bash
uv pip install pyinstaller
uv run pyinstaller smart_dictate.spec
```

The app bundle is created at `dist/SmartDictate.app`.

## Notes
- macOS will prompt for microphone permissions on first capture.
- Model download and caching will be implemented on first run.
- Default base folder is `~/SmartDictate` with subfolders like `records` and `transcripts`.
- Logs are written to `~/SmartDictate/smart-dictate.log`.
- Default hotkey is `Fn+Ctrl`, WAV output is 16 kHz mono.
- Default model is `mlx-community/whisper-large-v3-mlx`.
- Global hotkeys use the Accessibility permission.
- Recordings are written as timestamped WAV files in `~/SmartDictate/records`.
- Transcripts are stored as JSON with the same basename in `~/SmartDictate/records`.
- After transcription, text is copied to clipboard and pasted into the active field.
- JSON schema includes `id`, `text`, `original_text`, and `polished_text` (placeholders for future polishing).
- If the model repo is private/gated, set `HF_TOKEN` (or `HUGGINGFACE_HUB_TOKEN`) in your environment.
- Language auto-detection happens once per transcription run; for mixed-language dictation, audio is segmented on silence and each segment is auto-detected.
- By default, `condition_on_previous_text` is disabled to reduce language lock-in across long recordings.
- Optional word timestamps can enable hallucination filtering (`hallucination_silence_threshold`).
- Configuration is available from the menu bar and stored in `~/SmartDictate/config.toml`.
- Configuration allows selecting preferred language and model; models are fetched from `mlx-community/*` with `whisper` in the name.
- Downloaded models are listed separately and can be deleted from the configuration window.
- Hotkey can be changed in the configuration window (press Esc to cancel capture).
