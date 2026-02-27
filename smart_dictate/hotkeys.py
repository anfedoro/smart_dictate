from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

import Quartz

DEFAULT_HOTKEY_MODIFIERS = (
    Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskSecondaryFn
)
CG_MODIFIER_MASK = (
    Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskAlternate
    | Quartz.kCGEventFlagMaskShift
    | Quartz.kCGEventFlagMaskCommand
    | Quartz.kCGEventFlagMaskSecondaryFn
)
KEYCODE_FIELD = Quartz.kCGKeyboardEventKeycode
FN_DOUBLE_TAP_SECONDS = 0.2


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    keycode: int | None = None


class HotkeyManager:
    def __init__(self, on_toggle: Callable[[], None]) -> None:
        self._on_toggle = on_toggle
        self._hotkey_down = False
        self._fn_down = False
        self._fn_last_press = 0.0
        self._tap = None
        self._source = None
        self._run_loop = None
        self._registered: list[Hotkey] = []

    def register(self, hotkey: Hotkey) -> None:
        self._registered = [hotkey]
        self._hotkey_down = False

    def start(self) -> None:
        if self._tap is not None:
            return
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            self._event_callback,
            None,
        )
        if self._tap is None:
            raise RuntimeError("Failed to create event tap. Check Accessibility permissions.")
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._run_loop,
            self._source,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(self._tap, True)

    def stop(self) -> None:
        if self._tap is None:
            return
        Quartz.CGEventTapEnable(self._tap, False)
        if self._run_loop is not None and self._source is not None:
            Quartz.CFRunLoopRemoveSource(
                self._run_loop,
                self._source,
                Quartz.kCFRunLoopCommonModes,
            )
        self._tap = None
        self._source = None
        self._run_loop = None

    def _event_callback(self, _proxy, event_type, event, _refcon):
        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        if event_type not in (
            Quartz.kCGEventFlagsChanged,
            Quartz.kCGEventKeyDown,
            Quartz.kCGEventKeyUp,
        ):
            return event

        flags = Quartz.CGEventGetFlags(event) & CG_MODIFIER_MASK
        hotkey = self._registered[0] if self._registered else Hotkey(DEFAULT_HOTKEY_MODIFIERS)
        if hotkey.keycode is None:
            if event_type != Quartz.kCGEventFlagsChanged:
                return event
            if hotkey.modifiers == Quartz.kCGEventFlagMaskSecondaryFn:
                fn_now = (flags & Quartz.kCGEventFlagMaskSecondaryFn) != 0
                if fn_now and not self._fn_down:
                    now = time.monotonic()
                    if now - self._fn_last_press <= FN_DOUBLE_TAP_SECONDS:
                        self._on_toggle()
                        self._fn_last_press = 0.0
                    else:
                        self._fn_last_press = now
                    self._fn_down = True
                elif not fn_now and self._fn_down:
                    self._fn_down = False
                return event
            hotkey_now = flags == hotkey.modifiers
            if hotkey_now and not self._hotkey_down:
                self._hotkey_down = True
                self._on_toggle()
            elif not hotkey_now and self._hotkey_down:
                self._hotkey_down = False
            return event
        keycode = Quartz.CGEventGetIntegerValueField(event, KEYCODE_FIELD)
        if event_type == Quartz.kCGEventKeyUp:
            if keycode == hotkey.keycode:
                self._hotkey_down = False
            return event
        if event_type == Quartz.kCGEventKeyDown:
            if keycode == hotkey.keycode and flags == hotkey.modifiers and not self._hotkey_down:
                self._hotkey_down = True
                self._on_toggle()
        return event


def format_hotkey(modifiers: int, keycode: int | None, key_label: str | None = None) -> str:
    names = []
    if modifiers & Quartz.kCGEventFlagMaskControl:
        names.append("Ctrl")
    if modifiers & Quartz.kCGEventFlagMaskAlternate:
        names.append("Alt")
    if modifiers & Quartz.kCGEventFlagMaskShift:
        names.append("Shift")
    if modifiers & Quartz.kCGEventFlagMaskCommand:
        names.append("Cmd")
    if modifiers & Quartz.kCGEventFlagMaskSecondaryFn:
        names.append("Fn")
    if keycode is None:
        return "+".join(names) if names else "None"
    label = key_label or _keycode_label(keycode)
    if label:
        names.append(label)
    return "+".join(names) if names else label


def _keycode_label(keycode: int) -> str:
    mapping = {
        36: "Enter",
        48: "Tab",
        49: "Space",
        51: "Backspace",
        53: "Esc",
        57: "CapsLock",
        123: "Left",
        124: "Right",
        125: "Down",
        126: "Up",
    }
    if keycode in mapping:
        return mapping[keycode]
    if 122 <= keycode <= 133:
        return f"F{keycode - 111}"
    return f"Key{keycode}"
