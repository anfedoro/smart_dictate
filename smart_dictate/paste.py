from __future__ import annotations

import time

from AppKit import NSPasteboard, NSPasteboardTypeString
import Quartz

COMMAND_V_KEYCODE = 9


def copy_text(text: str) -> None:
    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, NSPasteboardTypeString)


def paste_via_command_v() -> None:
    event_down = Quartz.CGEventCreateKeyboardEvent(None, COMMAND_V_KEYCODE, True)
    Quartz.CGEventSetFlags(event_down, Quartz.kCGEventFlagMaskCommand)
    event_up = Quartz.CGEventCreateKeyboardEvent(None, COMMAND_V_KEYCODE, False)
    Quartz.CGEventSetFlags(event_up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_up)


def paste_text(text: str) -> None:
    copy_text(text)
    time.sleep(0.05)
    paste_via_command_v()
