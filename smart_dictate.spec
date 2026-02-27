# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

project_root = Path.cwd()

mlx_datas, mlx_binaries, mlx_hidden = collect_all("mlx")
mlxw_datas, mlxw_binaries, mlxw_hidden = collect_all("mlx_whisper")
mlxw_hidden = [
    name
    for name in mlxw_hidden
    if not name.endswith("torch_whisper") and not name.endswith(".cli")
]
safe_datas, safe_binaries, safe_hidden = collect_all("safetensors")

hiddenimports = [
    "objc",
    "Quartz",
    "AppKit",
    "Foundation",
    "AVFoundation",
]
hiddenimports += mlx_hidden
hiddenimports += mlxw_hidden
hiddenimports += safe_hidden

datas = mlx_datas + mlxw_datas + safe_datas
binaries = mlx_binaries + mlxw_binaries + safe_binaries

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "scipy",
        "tensorflow",
        "tensorboard",
        "numba",
        "llvmlite",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    exclude_binaries=False,
    name="SmartDictate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

app = BUNDLE(
    exe,
    name="SmartDictate.app",
    icon="resources/SmartDictate.icns",
    bundle_identifier="com.anfedoro.smartdictate",
    info_plist={
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "SmartDictate needs microphone access to record dictation.",
    },
)
