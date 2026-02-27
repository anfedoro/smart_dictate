from __future__ import annotations

import logging
import os
import hashlib
import subprocess
import threading
import time
from pathlib import Path
import sys

import objc
import Quartz
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSAttributedString,
    NSButton,
    NSColor,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleInformational,
    NSControlStateValueOn,
    NSEvent,
    NSEventMaskFlagsChanged,
    NSEventMaskKeyDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagFunction,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSEventTypeFlagsChanged,
    NSEventTypeKeyDown,
    NSMenu,
    NSMenuItem,
    NSMakeRect,
    NSPopUpButton,
    NSScrollView,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSSecureTextField,
    NSTextField,
    NSTextView,
    NSSwitchButton,
    NSForegroundColorAttributeName,
)
from Foundation import NSObject, NSOperationQueue

from smart_dictate.audio_capture import AudioCapture
from smart_dictate.config import AppConfig, load_config, save_config
from smart_dictate.hotkeys import Hotkey, HotkeyManager, format_hotkey
from smart_dictate.languages import list_languages
from smart_dictate.keychain import get_postprocess_api_key, set_postprocess_api_key
from smart_dictate.login_item import ensure_login_item_start
from smart_dictate.model_manager import ensure_model
from smart_dictate.models_catalog import (
    delete_model,
    fetch_whisper_models,
    is_model_downloaded,
    list_downloaded_models,
)
from smart_dictate.paths import config_path, models_dir, records_dir
from smart_dictate.paste import paste_text
from smart_dictate.postprocess import PostprocessConfig, postprocess_text
from smart_dictate.settings import Settings
from smart_dictate.transcription import (
    transcribe_audio,
    unload_model,
    warmup_model,
    write_transcript_json,
)

NS_MODIFIER_MASK = (
    NSEventModifierFlagControl
    | NSEventModifierFlagOption
    | NSEventModifierFlagShift
    | NSEventModifierFlagCommand
    | NSEventModifierFlagFunction
)

BUNDLE_ID = "com.anfedoro.smartdictate"
PRESET_MODELS = [
    ("Base (~150 MB)", "mlx-community/whisper-base-mlx"),
    ("Small (~490 MB)", "mlx-community/whisper-small-mlx"),
    ("Medium (~1.5 GB)", "mlx-community/whisper-medium-mlx"),
    ("Large-v3 (~3.1 GB)", "mlx-community/whisper-large-v3-mlx"),
    ("Large-v3-Turbo (1.61 GB)", "mlx-community/whisper-large-v3-turbo"),
]
CUSTOM_MODEL_LABEL = "Custom (HF repo id)"


def _ns_flags_to_cg(flags: int) -> int:
    result = 0
    if flags & NSEventModifierFlagControl:
        result |= Quartz.kCGEventFlagMaskControl
    if flags & NSEventModifierFlagOption:
        result |= Quartz.kCGEventFlagMaskAlternate
    if flags & NSEventModifierFlagShift:
        result |= Quartz.kCGEventFlagMaskShift
    if flags & NSEventModifierFlagCommand:
        result |= Quartz.kCGEventFlagMaskCommand
    if flags & NSEventModifierFlagFunction:
        result |= Quartz.kCGEventFlagMaskSecondaryFn
    return result


class StatusBarController(NSObject):
    def initWithApp_(self, app: "DictateApp") -> "StatusBarController | None":
        self = objc.super(StatusBarController, self).init()
        if self is None:
            return None
        self._app = app
        self._status_item = (
            NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        )
        self._menu = NSMenu.alloc().init()
        self._toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start Recording",
            "toggleRecording:",
            "",
        )
        self._toggle_item.setTarget_(self)
        self._menu.addItem_(self._toggle_item)
        self._config_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Configuration...",
            "openConfiguration:",
            "",
        )
        self._config_item.setTarget_(self)
        self._menu.addItem_(self._config_item)
        self._menu.addItem_(NSMenuItem.separatorItem())
        self._quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit",
            "quit:",
            "q",
        )
        self._quit_item.setTarget_(self)
        self._menu.addItem_(self._quit_item)
        self._status_item.setMenu_(self._menu)
        self.update_indicator()
        return self

    def toggleRecording_(self, _sender) -> None:
        self._app.toggle_recording()

    def openConfiguration_(self, _sender) -> None:
        self._app.show_configuration()

    def quit_(self, _sender) -> None:
        NSApplication.sharedApplication().terminate_(None)

    def update_indicator(self) -> None:
        if self._app.recording:
            title = "•REC"
            color = NSColor.systemRedColor()
        elif self._app.model_loading:
            title = "SD"
            color = NSColor.systemGrayColor()
        elif self._app.transcribing:
            title = "SD..."
            color = NSColor.systemOrangeColor()
        else:
            title = "SD"
            color = NSColor.labelColor()
        self._set_status_title(title, color)
        if self._app.recording:
            self._toggle_item.setTitle_("Stop Recording")
            self._toggle_item.setEnabled_(True)
        elif self._app.model_loading:
            self._toggle_item.setTitle_("Loading Model...")
            self._toggle_item.setEnabled_(False)
        else:
            self._toggle_item.setTitle_("Start Recording")
            self._toggle_item.setEnabled_(True)

    def _set_status_title(self, title: str, color: NSColor) -> None:
        button = self._status_item.button()
        if button is None:
            self._status_item.setTitle_(title)
            return
        attributes = {NSForegroundColorAttributeName: color}
        attributed = NSAttributedString.alloc().initWithString_attributes_(title, attributes)
        button.setAttributedTitle_(attributed)


class ConfigWindowController(NSObject):
    def initWithApp_(self, app: "DictateApp") -> "ConfigWindowController | None":
        self = objc.super(ConfigWindowController, self).init()
        if self is None:
            return None
        self._app = app
        self._popup: NSPopUpButton | None = None
        self._model_popup: NSPopUpButton | None = None
        self._model_custom_label: NSTextField | None = None
        self._model_custom_field: NSTextField | None = None
        self._model_custom_hint: NSTextField | None = None
        self._downloaded_popup: NSPopUpButton | None = None
        self._delete_button: NSButton | None = None
        self._postprocess_enabled_button: NSButton | None = None
        self._postprocess_key_status_button: NSButton | None = None
        self._postprocess_set_key_button: NSButton | None = None
        self._postprocess_base_url_field: NSTextField | None = None
        self._postprocess_model_field: NSTextField | None = None
        self._postprocess_edit_prompt_button: NSButton | None = None
        self._postprocess_prompt_status_label: NSTextField | None = None
        self._idle_minutes_field: NSTextField | None = None
        self._hotkey_button: NSButton | None = None
        self._hotkey_monitor = None
        self._capturing_hotkey = False
        self._capture_last_flags = 0
        self._window = self._build_window()
        return self

    def show(self) -> None:
        self.refresh()
        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def refresh(self) -> None:
        if self._popup is None:
            return
        current = self._app.language_override
        index = 0
        for idx in range(self._popup.numberOfItems()):
            item = self._popup.itemAtIndex_(idx)
            if item is None:
                continue
            code = item.representedObject()
            if code is None and current is None:
                index = idx
                break
            if code is not None and current == str(code):
                index = idx
                break
        self._popup.selectItemAtIndex_(index)
        self._refresh_models()
        self._refresh_hotkey()
        self._refresh_model_idle()
        self._refresh_postprocess()

    def languageChanged_(self, _sender) -> None:
        if self._popup is None:
            return
        item = self._popup.selectedItem()
        if item is None:
            return
        code = item.representedObject()
        language = str(code) if code else None
        self._app.set_language_override(language)

    def modelChanged_(self, _sender) -> None:
        if self._model_popup is None:
            return
        item = self._model_popup.selectedItem()
        if item is None:
            return
        model_id = item.representedObject()
        if model_id is None:
            self._set_custom_model_visible(True)
            self._apply_custom_model()
            return
        self._set_custom_model_visible(False)
        self._app.set_model_override(str(model_id))
        self._refresh_models()

    def customModelChanged_(self, _sender) -> None:
        self._apply_custom_model()

    def postprocessEnabledChanged_(self, _sender) -> None:
        if self._postprocess_enabled_button is None:
            return
        enabled = self._postprocess_enabled_button.state() == NSControlStateValueOn
        self._app.set_postprocess_enabled(bool(enabled))
        self._refresh_postprocess()

    def postprocessBaseUrlChanged_(self, _sender) -> None:
        if self._postprocess_base_url_field is None:
            return
        value = self._postprocess_base_url_field.stringValue().strip()
        self._app.set_postprocess_base_url(value or None)
        self._refresh_postprocess()

    def postprocessModelChanged_(self, _sender) -> None:
        if self._postprocess_model_field is None:
            return
        value = self._postprocess_model_field.stringValue().strip()
        self._app.set_postprocess_model(value or None)
        self._refresh_postprocess()

    def postprocessSetKey_(self, _sender) -> None:
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleInformational)
        alert.setMessageText_("Set API key")
        alert.setInformativeText_("Paste the API key to store it in Keychain.")
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 24))
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        response = alert.runModal()
        if response != NSAlertFirstButtonReturn:
            return
        value = field.stringValue().strip()
        if not value:
            return
        self._app.set_postprocess_api_key(value)
        self._refresh_postprocess()

    def postprocessEditPrompt_(self, _sender) -> None:
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleInformational)
        alert.setMessageText_("Edit postprocess prompt")
        alert.setInformativeText_(
            "Set a custom system prompt used for post-processing."
        )
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 460, 220))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 440, 220))
        text_view.setString_(self._app.postprocess_system_prompt or "")
        scroll.setDocumentView_(text_view)
        alert.setAccessoryView_(scroll)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        response = alert.runModal()
        if response != NSAlertFirstButtonReturn:
            return
        value = str(text_view.string()).strip()
        self._app.set_postprocess_system_prompt(value or None)
        self._refresh_postprocess()

    def deleteModel_(self, _sender) -> None:
        if self._downloaded_popup is None:
            return
        item = self._downloaded_popup.selectedItem()
        if item is None:
            return
        model_id = item.representedObject()
        if model_id is None:
            return
        self._app.delete_downloaded_model(str(model_id))
        self._refresh_models()

    def hotkeyClicked_(self, _sender) -> None:
        if self._capturing_hotkey:
            return
        self._capturing_hotkey = True
        self._capture_last_flags = 0
        if self._hotkey_button is not None:
            self._hotkey_button.setTitle_("Press hotkey (Esc to cancel)")
        mask = NSEventMaskKeyDown | NSEventMaskFlagsChanged
        self._hotkey_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask,
            self._handle_hotkey_event,
        )

    def modelIdleChanged_(self, _sender) -> None:
        if self._idle_minutes_field is None:
            return
        value = self._idle_minutes_field.stringValue().strip()
        if not value:
            self._app.set_model_idle_minutes(None)
            self._refresh_model_idle()
            return
        try:
            minutes = int(value)
        except ValueError:
            self._refresh_model_idle()
            return
        if minutes < 0:
            minutes = 0
        self._app.set_model_idle_minutes(minutes)
        self._refresh_model_idle()

    def _build_window(self) -> NSWindow:
        frame = NSMakeRect(0, 0, 440, 600)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            style,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("SmartDictate Settings")
        window.setReleasedWhenClosed_(False)
        window.center()
        window.setDelegate_(self)
        content = window.contentView()
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 520, 160, 22))
        label.setStringValue_("Preferred language")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        if content is not None:
            content.addSubview_(label)
        self._popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(190, 516, 220, 26),
            False,
        )
        self._popup.setTarget_(self)
        self._popup.setAction_("languageChanged:")
        if content is not None:
            content.addSubview_(self._popup)
        self._populate_languages()
        model_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 480, 160, 22))
        model_label.setStringValue_("Model")
        model_label.setBezeled_(False)
        model_label.setDrawsBackground_(False)
        model_label.setEditable_(False)
        model_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(model_label)
        self._model_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(190, 476, 220, 26),
            False,
        )
        self._model_popup.setTarget_(self)
        self._model_popup.setAction_("modelChanged:")
        if content is not None:
            content.addSubview_(self._model_popup)
        self._model_custom_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 446, 160, 22)
        )
        self._model_custom_label.setStringValue_(CUSTOM_MODEL_LABEL)
        self._model_custom_label.setBezeled_(False)
        self._model_custom_label.setDrawsBackground_(False)
        self._model_custom_label.setEditable_(False)
        self._model_custom_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(self._model_custom_label)
        self._model_custom_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(190, 442, 220, 26)
        )
        self._model_custom_field.setPlaceholderString_(
            "mlx-community/whisper-..."
        )
        self._model_custom_field.setTarget_(self)
        self._model_custom_field.setAction_("customModelChanged:")
        if content is not None:
            content.addSubview_(self._model_custom_field)
        self._model_custom_hint = NSTextField.alloc().initWithFrame_(
            NSMakeRect(190, 422, 220, 18)
        )
        self._model_custom_hint.setStringValue_(
            "Use MLX-compatible Whisper model id"
        )
        self._model_custom_hint.setBezeled_(False)
        self._model_custom_hint.setDrawsBackground_(False)
        self._model_custom_hint.setEditable_(False)
        self._model_custom_hint.setSelectable_(False)
        self._model_custom_hint.setTextColor_(NSColor.secondaryLabelColor())
        if content is not None:
            content.addSubview_(self._model_custom_hint)
        downloaded_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 396, 160, 22))
        downloaded_label.setStringValue_("Downloaded models")
        downloaded_label.setBezeled_(False)
        downloaded_label.setDrawsBackground_(False)
        downloaded_label.setEditable_(False)
        downloaded_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(downloaded_label)
        self._downloaded_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(190, 392, 220, 26),
            False,
        )
        if content is not None:
            content.addSubview_(self._downloaded_popup)
        self._delete_button = NSButton.alloc().initWithFrame_(NSMakeRect(190, 356, 100, 26))
        self._delete_button.setTitle_("Delete")
        self._delete_button.setTarget_(self)
        self._delete_button.setAction_("deleteModel:")
        if content is not None:
            content.addSubview_(self._delete_button)
        self._postprocess_enabled_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(20, 316, 220, 22)
        )
        self._postprocess_enabled_button.setButtonType_(NSSwitchButton)
        self._postprocess_enabled_button.setTitle_("Enable post-processing")
        self._postprocess_enabled_button.setTarget_(self)
        self._postprocess_enabled_button.setAction_("postprocessEnabledChanged:")
        if content is not None:
            content.addSubview_(self._postprocess_enabled_button)
        postprocess_url_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 286, 160, 22)
        )
        postprocess_url_label.setStringValue_("Postprocess base URL")
        postprocess_url_label.setBezeled_(False)
        postprocess_url_label.setDrawsBackground_(False)
        postprocess_url_label.setEditable_(False)
        postprocess_url_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(postprocess_url_label)
        self._postprocess_base_url_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(190, 282, 220, 26)
        )
        self._postprocess_base_url_field.setPlaceholderString_("https://api.openai.com")
        self._postprocess_base_url_field.setTarget_(self)
        self._postprocess_base_url_field.setAction_("postprocessBaseUrlChanged:")
        if content is not None:
            content.addSubview_(self._postprocess_base_url_field)
        postprocess_model_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 256, 160, 22)
        )
        postprocess_model_label.setStringValue_("Postprocess model")
        postprocess_model_label.setBezeled_(False)
        postprocess_model_label.setDrawsBackground_(False)
        postprocess_model_label.setEditable_(False)
        postprocess_model_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(postprocess_model_label)
        self._postprocess_model_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(190, 252, 220, 26)
        )
        self._postprocess_model_field.setPlaceholderString_("gpt-4o-mini")
        self._postprocess_model_field.setTarget_(self)
        self._postprocess_model_field.setAction_("postprocessModelChanged:")
        if content is not None:
            content.addSubview_(self._postprocess_model_field)
        self._postprocess_key_status_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(20, 226, 220, 22)
        )
        self._postprocess_key_status_button.setButtonType_(NSSwitchButton)
        self._postprocess_key_status_button.setTitle_("API key stored in Keychain")
        self._postprocess_key_status_button.setEnabled_(False)
        if content is not None:
            content.addSubview_(self._postprocess_key_status_button)
        self._postprocess_set_key_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(190, 194, 220, 26)
        )
        self._postprocess_set_key_button.setTitle_("Set API key")
        self._postprocess_set_key_button.setTarget_(self)
        self._postprocess_set_key_button.setAction_("postprocessSetKey:")
        if content is not None:
            content.addSubview_(self._postprocess_set_key_button)
        postprocess_prompt_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 166, 160, 22)
        )
        postprocess_prompt_label.setStringValue_("Postprocess prompt")
        postprocess_prompt_label.setBezeled_(False)
        postprocess_prompt_label.setDrawsBackground_(False)
        postprocess_prompt_label.setEditable_(False)
        postprocess_prompt_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(postprocess_prompt_label)
        self._postprocess_edit_prompt_button = NSButton.alloc().initWithFrame_(
            NSMakeRect(190, 162, 220, 26)
        )
        self._postprocess_edit_prompt_button.setTitle_("Edit prompt")
        self._postprocess_edit_prompt_button.setTarget_(self)
        self._postprocess_edit_prompt_button.setAction_("postprocessEditPrompt:")
        if content is not None:
            content.addSubview_(self._postprocess_edit_prompt_button)
        self._postprocess_prompt_status_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(190, 140, 220, 18)
        )
        self._postprocess_prompt_status_label.setBezeled_(False)
        self._postprocess_prompt_status_label.setDrawsBackground_(False)
        self._postprocess_prompt_status_label.setEditable_(False)
        self._postprocess_prompt_status_label.setSelectable_(False)
        self._postprocess_prompt_status_label.setTextColor_(NSColor.secondaryLabelColor())
        if content is not None:
            content.addSubview_(self._postprocess_prompt_status_label)
        idle_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 50, 160, 22))
        idle_label.setStringValue_("Unload model after (min)")
        idle_label.setBezeled_(False)
        idle_label.setDrawsBackground_(False)
        idle_label.setEditable_(False)
        idle_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(idle_label)
        self._idle_minutes_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(190, 46, 220, 26)
        )
        self._idle_minutes_field.setTarget_(self)
        self._idle_minutes_field.setAction_("modelIdleChanged:")
        if content is not None:
            content.addSubview_(self._idle_minutes_field)
        hotkey_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 10, 160, 22))
        hotkey_label.setStringValue_("Hotkey")
        hotkey_label.setBezeled_(False)
        hotkey_label.setDrawsBackground_(False)
        hotkey_label.setEditable_(False)
        hotkey_label.setSelectable_(False)
        if content is not None:
            content.addSubview_(hotkey_label)
        self._hotkey_button = NSButton.alloc().initWithFrame_(NSMakeRect(190, 6, 220, 26))
        self._hotkey_button.setTitle_("Set hotkey")
        self._hotkey_button.setTarget_(self)
        self._hotkey_button.setAction_("hotkeyClicked:")
        if content is not None:
            content.addSubview_(self._hotkey_button)
        self._refresh_models()
        return window

    def _populate_languages(self) -> None:
        if self._popup is None:
            return
        self._popup.removeAllItems()
        self._popup.addItemWithTitle_("Auto")
        auto_item = self._popup.itemAtIndex_(0)
        if auto_item is not None:
            auto_item.setRepresentedObject_(None)
        for code, name in list_languages():
            title = f"{name} ({code})"
            self._popup.addItemWithTitle_(title)
            item = self._popup.lastItem()
            if item is not None:
                item.setRepresentedObject_(code)

    def _refresh_models(self) -> None:
        if self._model_popup is None or self._downloaded_popup is None:
            return
        self._model_popup.removeAllItems()
        for title, model_id in PRESET_MODELS:
            self._model_popup.addItemWithTitle_(title)
            item = self._model_popup.lastItem()
            if item is not None:
                item.setRepresentedObject_(model_id)
        self._model_popup.addItemWithTitle_("Custom...")
        custom_item = self._model_popup.lastItem()
        if custom_item is not None:
            custom_item.setRepresentedObject_(None)
        current = self._app.current_model_id
        custom_selected = True
        for idx, (_, model_id) in enumerate(PRESET_MODELS):
            if model_id == current:
                self._model_popup.selectItemAtIndex_(idx)
                custom_selected = False
                break
        if custom_selected:
            self._model_popup.selectItemAtIndex_(len(PRESET_MODELS))
        self._set_custom_model_visible(custom_selected)
        if custom_selected and self._model_custom_field is not None:
            self._model_custom_field.setStringValue_(current or "")
        self._downloaded_popup.removeAllItems()
        downloaded = self._app.downloaded_models
        if not downloaded:
            self._downloaded_popup.addItemWithTitle_("None")
            none_item = self._downloaded_popup.itemAtIndex_(0)
            if none_item is not None:
                none_item.setRepresentedObject_(None)
        else:
            for model_id in downloaded:
                self._downloaded_popup.addItemWithTitle_(model_id)
                item = self._downloaded_popup.lastItem()
                if item is not None:
                    item.setRepresentedObject_(model_id)
        has_downloaded = bool(downloaded)
        if self._delete_button is not None:
            self._delete_button.setEnabled_(has_downloaded)

    def _set_custom_model_visible(self, visible: bool) -> None:
        if self._model_custom_label is not None:
            self._model_custom_label.setHidden_(not visible)
        if self._model_custom_field is not None:
            self._model_custom_field.setHidden_(not visible)
        if self._model_custom_hint is not None:
            self._model_custom_hint.setHidden_(not visible)

    def _apply_custom_model(self) -> None:
        if self._model_custom_field is None:
            return
        value = self._model_custom_field.stringValue().strip()
        if not value:
            return
        self._app.set_model_override(value)
        self._refresh_models()

    def _refresh_hotkey(self) -> None:
        if self._hotkey_button is None or self._capturing_hotkey:
            return
        title = format_hotkey(
            self._app.hotkey_modifiers,
            self._app.hotkey_keycode,
            self._app.hotkey_label,
        )
        self._hotkey_button.setTitle_(title)

    def _refresh_model_idle(self) -> None:
        if self._idle_minutes_field is None:
            return
        minutes = self._app.model_idle_minutes
        self._idle_minutes_field.setStringValue_(str(minutes))

    def _refresh_postprocess(self) -> None:
        if self._postprocess_enabled_button is None:
            return
        default_config = PostprocessConfig()
        enabled = self._app.postprocess_enabled
        self._postprocess_enabled_button.setState_(
            NSControlStateValueOn if enabled else 0
        )
        if self._postprocess_base_url_field is not None:
            self._postprocess_base_url_field.setStringValue_(
                self._app.postprocess_base_url or default_config.base_url
            )
        if self._postprocess_model_field is not None:
            self._postprocess_model_field.setStringValue_(
                self._app.postprocess_model or default_config.model
            )
        if self._postprocess_key_status_button is not None:
            has_key = self._app.postprocess_api_key_set
            self._postprocess_key_status_button.setState_(
                NSControlStateValueOn if has_key else 0
            )
        prompt = self._app.postprocess_system_prompt or ""
        if self._postprocess_edit_prompt_button is not None:
            title = "Edit prompt (custom)" if prompt else "Edit prompt"
            self._postprocess_edit_prompt_button.setTitle_(title)
        if self._postprocess_prompt_status_label is not None:
            if prompt:
                preview = " ".join(prompt.split())
                if len(preview) > 44:
                    preview = f"{preview[:41]}..."
                self._postprocess_prompt_status_label.setStringValue_(preview)
                self._postprocess_prompt_status_label.setToolTip_(prompt)
            else:
                self._postprocess_prompt_status_label.setStringValue_("Using default prompt")
                self._postprocess_prompt_status_label.setToolTip_(None)
        self._set_postprocess_fields_enabled(enabled)

    def _set_postprocess_fields_enabled(self, enabled: bool) -> None:
        fields = [
            self._postprocess_base_url_field,
            self._postprocess_model_field,
            self._postprocess_edit_prompt_button,
        ]
        for field in fields:
            if field is not None:
                field.setEnabled_(enabled)
        if self._postprocess_set_key_button is not None:
            self._postprocess_set_key_button.setEnabled_(enabled)

    def _handle_hotkey_event(self, event):
        if not self._capturing_hotkey:
            return event
        event_type = event.type()
        if event_type == NSEventTypeKeyDown:
            keycode = event.keyCode()
            if keycode == 53:
                self._stop_hotkey_capture()
                return None
            modifiers = _ns_flags_to_cg(int(event.modifierFlags()) & NS_MODIFIER_MASK)
            label = event.charactersIgnoringModifiers() or None
            if label:
                label = label.strip()
            self._app.set_hotkey(modifiers, keycode, label)
            self._stop_hotkey_capture()
            return None
        if event_type == NSEventTypeFlagsChanged:
            flags = int(event.modifierFlags()) & NS_MODIFIER_MASK
            if flags != 0:
                self._capture_last_flags |= _ns_flags_to_cg(flags)
            else:
                if self._capture_last_flags:
                    self._app.set_hotkey(self._capture_last_flags, None, None)
                    self._stop_hotkey_capture()
            return None
        return event

    def _stop_hotkey_capture(self) -> None:
        if self._hotkey_monitor is not None:
            NSEvent.removeMonitor_(self._hotkey_monitor)
            self._hotkey_monitor = None
        self._capturing_hotkey = False
        self._capture_last_flags = 0
        self._refresh_hotkey()

    def windowWillClose_(self, _notification) -> None:
        self._stop_hotkey_capture()


class DictateApp:
    def __init__(self) -> None:
        self._recording = False
        self._controller: StatusBarController | None = None
        self._config_window: ConfigWindowController | None = None
        self._model_loading = False
        self._warmup_id = 0
        self._pending_stop_timer: threading.Timer | None = None
        self._pending_stop_path: Path | None = None
        self._pending_stop_lock = threading.Lock()
        self._stop_cancel_window_seconds = 0.4
        self._transcribing_count = 0
        self._transcribing_lock = threading.Lock()
        self._pending_permission_notice = False
        self._accessibility_watch_active = False
        self._model_idle_minutes: int | None = None
        self._model_idle_seconds = 0
        self._model_idle_timer: threading.Timer | None = None
        self._model_idle_lock = threading.Lock()
        self._last_model_use = 0.0
        self._last_model_id: str | None = None
        self._total_memory_bytes = self._get_total_memory_bytes()
        self._defer_warmup = (
            self._total_memory_bytes is not None
            and self._total_memory_bytes < 16 * 1024 * 1024 * 1024
        )
        self._settings = Settings()
        self._audio = AudioCapture(
            output_dir=records_dir(),
            sample_rate_hz=self._settings.sample_rate_hz,
            channels=self._settings.channels,
        )
        self._config_path = config_path()
        self._config = load_config(self._config_path)
        self._app_hash = self._compute_app_hash()
        self._language_override = self._config.language
        self._model_override = self._config.model_id
        self._postprocess_enabled = self._config.postprocess_enabled
        self._postprocess_base_url = self._config.postprocess_base_url
        self._postprocess_model = self._config.postprocess_model
        self._postprocess_system_prompt = self._config.postprocess_system_prompt
        self._model_idle_minutes = self._config.model_idle_minutes
        self._model_idle_seconds = self._compute_model_idle_seconds(self._model_idle_minutes)
        self._hotkey_modifiers = (
            self._config.hotkey_modifiers
            if self._config.hotkey_modifiers is not None
            else self._settings.hotkey_modifiers
        )
        self._hotkey_keycode = (
            self._config.hotkey_keycode
            if self._config.hotkey_keycode is not None
            else self._settings.hotkey_keycode
        )
        self._hotkey_label = self._config.hotkey_label
        self._model_catalog: list[str] = []
        self._downloaded_models = list_downloaded_models(models_dir())
        self._catalog_loaded = False
        self._hotkeys = HotkeyManager(self.toggle_recording)
        self._hotkeys.register(
            Hotkey(modifiers=self._hotkey_modifiers, keycode=self._hotkey_keycode)
        )
        self._reset_permissions_on_start()
        ensure_login_item_start()
        if self._defer_warmup:
            logging.getLogger(__name__).info(
                "Deferring model warmup due to low RAM (%s bytes).",
                self._total_memory_bytes,
            )
            self._start_model_download(self.current_model_id, warmup=False)
        else:
            self._start_model_warmup(self.current_model_id)

    @staticmethod
    def _get_total_memory_bytes() -> int | None:
        try:
            output = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            return int(output)
        except Exception:
            try:
                pages = os.sysconf("SC_PHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                return int(pages) * int(page_size)
            except Exception:
                return None

    def _compute_app_hash(self) -> str | None:
        try:
            if getattr(sys, "frozen", False):
                path = Path(sys.executable)
            else:
                path = Path(__file__).resolve()
        except Exception:
            return None
        if not path.exists():
            return None
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
        except Exception:
            return None
        return hasher.hexdigest()

    def _build_postprocess_config(self) -> PostprocessConfig:
        default_config = PostprocessConfig()
        return PostprocessConfig(
            enabled=self._postprocess_enabled,
            base_url=self._postprocess_base_url or default_config.base_url,
            model=self._postprocess_model or default_config.model,
            system_prompt=self._postprocess_system_prompt or default_config.system_prompt,
            timeout_seconds=default_config.timeout_seconds,
        )

    def _save_config_state(self) -> None:
        self._config = AppConfig(
            language=self._language_override,
            model_id=self._model_override,
            model_idle_minutes=self._model_idle_minutes,
            app_hash=self._app_hash,
            postprocess_enabled=self._postprocess_enabled,
            postprocess_base_url=self._postprocess_base_url,
            postprocess_model=self._postprocess_model,
            postprocess_system_prompt=self._postprocess_system_prompt,
            hotkey_modifiers=self._hotkey_modifiers,
            hotkey_keycode=self._hotkey_keycode,
            hotkey_label=self._hotkey_label,
        )
        save_config(self._config_path, self._config)

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def language_override(self) -> str | None:
        return self._language_override

    @property
    def model_loading(self) -> bool:
        return self._model_loading

    @property
    def transcribing(self) -> bool:
        with self._transcribing_lock:
            return self._transcribing_count > 0

    def _default_model_idle_minutes(self) -> int:
        if (
            self._total_memory_bytes is not None
            and self._total_memory_bytes < 16 * 1024 * 1024 * 1024
        ):
            return 15
        return 0

    def _compute_model_idle_seconds(self, minutes: int | None) -> int:
        if minutes is None:
            minutes = self._default_model_idle_minutes()
        if minutes <= 0:
            return 0
        return minutes * 60

    @property
    def model_idle_minutes(self) -> int:
        if self._model_idle_minutes is None:
            return self._default_model_idle_minutes()
        if self._model_idle_minutes < 0:
            return 0
        return self._model_idle_minutes

    def set_language_override(self, language: str | None) -> None:
        self._language_override = language
        self._save_config_state()

    @property
    def model_catalog(self) -> list[str]:
        return self._model_catalog

    @property
    def downloaded_models(self) -> list[str]:
        return self._downloaded_models

    @property
    def current_model_id(self) -> str:
        return self._model_override or self._settings.model_id

    def set_model_override(self, model_id: str) -> None:
        if model_id == self._model_override:
            return
        self._model_override = model_id
        self._save_config_state()
        self._start_model_download(model_id)

    @property
    def hotkey_modifiers(self) -> int:
        return self._hotkey_modifiers

    @property
    def hotkey_keycode(self) -> int | None:
        return self._hotkey_keycode

    @property
    def hotkey_label(self) -> str | None:
        return self._hotkey_label

    @property
    def postprocess_enabled(self) -> bool:
        return self._postprocess_enabled

    @property
    def postprocess_base_url(self) -> str | None:
        return self._postprocess_base_url

    @property
    def postprocess_model(self) -> str | None:
        return self._postprocess_model

    @property
    def postprocess_api_key_set(self) -> bool:
        return get_postprocess_api_key() is not None

    @property
    def postprocess_system_prompt(self) -> str | None:
        return self._postprocess_system_prompt

    def set_hotkey(self, modifiers: int, keycode: int | None, label: str | None) -> None:
        self._hotkey_modifiers = modifiers
        self._hotkey_keycode = keycode
        self._hotkey_label = label
        self._hotkeys.register(Hotkey(modifiers=modifiers, keycode=keycode))
        self._save_config_state()

    def set_postprocess_enabled(self, enabled: bool) -> None:
        self._postprocess_enabled = bool(enabled)
        self._save_config_state()

    def set_postprocess_base_url(self, base_url: str | None) -> None:
        self._postprocess_base_url = base_url
        self._save_config_state()

    def set_postprocess_model(self, model: str | None) -> None:
        self._postprocess_model = model
        self._save_config_state()

    def set_postprocess_api_key(self, api_key: str) -> None:
        set_postprocess_api_key(api_key)

    def set_postprocess_system_prompt(self, prompt: str | None) -> None:
        self._postprocess_system_prompt = prompt
        self._save_config_state()

    def set_model_idle_minutes(self, minutes: int | None) -> None:
        if minutes is not None and minutes < 0:
            minutes = 0
        self._model_idle_minutes = minutes
        self._model_idle_seconds = self._compute_model_idle_seconds(minutes)
        self._save_config_state()
        self._schedule_model_unload()

    def reset_permission(self, service: str) -> bool:
        try:
            result = subprocess.run(
                ["tccutil", "reset", service, BUNDLE_ID],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                logging.getLogger(__name__).info(
                    "Reset %s permission for %s",
                    service,
                    BUNDLE_ID,
                )
            else:
                logging.getLogger(__name__).warning(
                    "tccutil reset %s failed with exit code %s",
                    service,
                    result.returncode,
                )
            return result.returncode == 0
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to reset %s permission: %s",
                service,
                exc,
            )
            return False

    def _reset_permissions_on_start(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        if not self._app_hash:
            return
        if self._config.app_hash == self._app_hash:
            return
        self.reset_permission("Microphone")
        self.reset_permission("Accessibility")
        self._pending_permission_notice = True
        self._save_config_state()

    def _is_accessibility_trusted(self) -> bool:
        try:
            return bool(Quartz.AXIsProcessTrusted())
        except Exception:
            return False

    def _open_accessibility_settings(self) -> None:
        try:
            subprocess.run(
                [
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to open Accessibility settings: %s",
                exc,
            )

    def _watch_accessibility_and_enable_hotkeys(self) -> None:
        try:
            while True:
                if self._is_accessibility_trusted():
                    NSOperationQueue.mainQueue().addOperationWithBlock_(
                        self._enable_hotkeys_after_accessibility_granted
                    )
                    return
                time.sleep(1.0)
        finally:
            self._accessibility_watch_active = False

    def _enable_hotkeys_after_accessibility_granted(self) -> None:
        self._clear_pending_permission_notice()
        try:
            self._hotkeys.start()
        except RuntimeError as exc:
            logging.getLogger(__name__).warning(
                "Failed to activate hotkeys after Accessibility grant: %s",
                exc,
            )
            self._pending_permission_notice = True
            self._save_config_state()
            return
        if self._controller is not None:
            self._controller.update_indicator()

    def _start_accessibility_watch(self) -> None:
        if self._accessibility_watch_active:
            return
        self._accessibility_watch_active = True
        thread = threading.Thread(
            target=self._watch_accessibility_and_enable_hotkeys,
            daemon=True,
        )
        thread.start()

    def _clear_pending_permission_notice(self) -> None:
        if not self._pending_permission_notice:
            return
        self._pending_permission_notice = False

    def _show_permission_notice(self) -> None:
        if self._is_accessibility_trusted():
            self._clear_pending_permission_notice()
            return
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleInformational)
        alert.setMessageText_("Enable Accessibility")
        alert.setInformativeText_(
            "Accessibility permission was reset for this app version. "
            "Open System Settings → Privacy & Security → Accessibility and enable SmartDictate. "
            "Hotkeys will be activated automatically after access is granted."
        )
        alert.addButtonWithTitle_("Open Accessibility Settings")
        alert.addButtonWithTitle_("Later")
        response = alert.runModal()
        if response != NSAlertFirstButtonReturn:
            return
        self._open_accessibility_settings()
        self._start_accessibility_watch()

    def delete_downloaded_model(self, model_id: str) -> None:
        delete_model(models_dir(), model_id)
        self._downloaded_models = list_downloaded_models(models_dir())

    def show_configuration(self) -> None:
        if self._config_window is None:
            self._config_window = ConfigWindowController.alloc().initWithApp_(self)
        if self._config_window is not None:
            self._config_window.show()

    def ensure_model_catalog(self) -> None:
        if not self._catalog_loaded:
            self._start_model_catalog_fetch()

    def _start_model_catalog_fetch(self) -> None:
        if self._catalog_loaded:
            return
        thread = threading.Thread(target=self._load_model_catalog, daemon=True)
        thread.start()

    def _load_model_catalog(self) -> None:
        self._model_catalog = fetch_whisper_models()
        self._catalog_loaded = True
        self._schedule_ui_refresh()

    def _start_model_warmup(self, model_id: str) -> None:
        if not model_id:
            return
        self._warmup_id += 1
        warmup_id = self._warmup_id
        self._set_model_loading(True)
        thread = threading.Thread(
            target=self._warmup_model,
            args=(model_id, warmup_id),
            daemon=True,
        )
        thread.start()

    def _warmup_model(self, model_id: str, warmup_id: int) -> None:
        logger = logging.getLogger(__name__)
        try:
            warmup_model(model_id)
            self._mark_model_used(model_id)
        except Exception as exc:
            logger.error("Model warmup failed: %s", exc)
        finally:
            if warmup_id == self._warmup_id:
                self._set_model_loading(False)

    def _start_model_download(self, model_id: str, *, warmup: bool | None = None) -> None:
        if warmup is None:
            warmup = not self._defer_warmup
        self._warmup_id += 1
        warmup_id = self._warmup_id
        self._set_model_loading(True)
        if is_model_downloaded(models_dir(), model_id):
            self._downloaded_models = list_downloaded_models(models_dir())
            self._schedule_ui_refresh()
            if warmup:
                thread = threading.Thread(
                    target=self._warmup_model,
                    args=(model_id, warmup_id),
                    daemon=True,
                )
                thread.start()
            else:
                self._set_model_loading(False)
            return
        thread = threading.Thread(
            target=self._download_model,
            args=(model_id, warmup_id, warmup),
            daemon=True,
        )
        thread.start()

    def _download_model(self, model_id: str, warmup_id: int, warmup: bool) -> None:
        logger = logging.getLogger(__name__)
        try:
            ensure_model(model_id, models_dir())
        except Exception as exc:
            logger.error("Model download failed: %s", exc)
            if warmup_id == self._warmup_id:
                self._set_model_loading(False)
            return
        if warmup:
            try:
                warmup_model(model_id)
                self._mark_model_used(model_id)
            except Exception as exc:
                logger.error("Model warmup failed: %s", exc)
        self._downloaded_models = list_downloaded_models(models_dir())
        self._schedule_ui_refresh()
        if warmup_id == self._warmup_id:
            self._set_model_loading(False)

    def _schedule_ui_refresh(self) -> None:
        if self._config_window is None:
            return
        def refresh():
            if self._config_window is not None:
                self._config_window.refresh()

        NSOperationQueue.mainQueue().addOperationWithBlock_(refresh)

    def _schedule_status_refresh(self) -> None:
        if self._controller is None:
            return
        def refresh():
            if self._controller is not None:
                self._controller.update_indicator()

        NSOperationQueue.mainQueue().addOperationWithBlock_(refresh)

    def _set_model_loading(self, loading: bool) -> None:
        if self._model_loading == loading:
            return
        self._model_loading = loading
        self._schedule_status_refresh()

    def _increment_transcribing(self) -> None:
        with self._transcribing_lock:
            self._transcribing_count += 1
        self._schedule_status_refresh()

    def _decrement_transcribing(self) -> None:
        with self._transcribing_lock:
            if self._transcribing_count > 0:
                self._transcribing_count -= 1
        self._schedule_status_refresh()

    def _mark_model_used(self, model_id: str) -> None:
        with self._model_idle_lock:
            self._last_model_use = time.monotonic()
            self._last_model_id = model_id
        self._schedule_model_unload()

    def _schedule_model_unload(self) -> None:
        with self._model_idle_lock:
            if self._model_idle_timer is not None:
                self._model_idle_timer.cancel()
                self._model_idle_timer = None
            if self._model_idle_seconds <= 0:
                return
            timer = threading.Timer(
                self._model_idle_seconds,
                self._handle_model_idle_timeout,
            )
            timer.daemon = True
            self._model_idle_timer = timer
            timer.start()

    def _handle_model_idle_timeout(self) -> None:
        with self._model_idle_lock:
            if self._model_idle_seconds <= 0:
                return
            last_use = self._last_model_use
            last_model_id = self._last_model_id
        if last_model_id is None:
            return
        if time.monotonic() - last_use < self._model_idle_seconds:
            self._schedule_model_unload()
            return
        if self._recording or self.transcribing or self._model_loading:
            self._schedule_model_unload()
            return
        if last_model_id != self.current_model_id:
            self._schedule_model_unload()
            return
        if unload_model(last_model_id):
            logging.getLogger(__name__).info(
                "Model unloaded after idle timeout: %s",
                last_model_id,
            )

    def toggle_recording(self) -> None:
        if not self._recording:
            if self._cancel_pending_transcription():
                return
            if self._model_loading:
                logging.getLogger(__name__).info(
                    "Recording ignored while model is loading."
                )
                return
        if self._recording:
            self._recording = False
            try:
                path = self._audio.stop()
            except RuntimeError as exc:
                logging.getLogger(__name__).error("%s", exc)
                path = None
            if path is not None:
                logging.getLogger(__name__).info("Recording stopped: %s", path)
                self._schedule_transcription(path)
        else:
            try:
                path = self._audio.start()
            except RuntimeError as exc:
                logging.getLogger(__name__).error("%s", exc)
                path = None
            if path is not None:
                self._recording = True
                logging.getLogger(__name__).info("Recording started: %s", path)
        if self._controller is not None:
            self._controller.update_indicator()

    def _schedule_transcription(self, audio_path: Path) -> None:
        with self._pending_stop_lock:
            if self._pending_stop_timer is not None:
                self._pending_stop_timer.cancel()
            self._pending_stop_path = audio_path
            timer = threading.Timer(
                self._stop_cancel_window_seconds,
                self._finalize_transcription,
                args=(audio_path,),
            )
            timer.daemon = True
            self._pending_stop_timer = timer
            timer.start()

    def _finalize_transcription(self, audio_path: Path) -> None:
        with self._pending_stop_lock:
            if self._pending_stop_path != audio_path:
                return
            self._pending_stop_timer = None
            self._pending_stop_path = None
        self._start_transcription(audio_path)

    def _cancel_pending_transcription(self) -> bool:
        with self._pending_stop_lock:
            if self._pending_stop_timer is None or self._pending_stop_path is None:
                return False
            timer = self._pending_stop_timer
            audio_path = self._pending_stop_path
            self._pending_stop_timer = None
            self._pending_stop_path = None
        timer.cancel()
        logger = logging.getLogger(__name__)
        try:
            audio_path.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("Failed to delete canceled recording: %s", exc)
        logger.info("Recording canceled: %s", audio_path)
        return True

    def _start_transcription(self, audio_path) -> None:
        thread = threading.Thread(
            target=self._transcribe_and_paste,
            args=(audio_path,),
            daemon=True,
        )
        thread.start()

    def _transcribe_and_paste(self, audio_path) -> None:
        logger = logging.getLogger(__name__)
        self._increment_transcribing()
        try:
            self._mark_model_used(self.current_model_id)
            text = transcribe_audio(
                audio_path,
                self.current_model_id,
                sample_rate_hz=self._settings.sample_rate_hz,
                condition_on_previous_text=self._settings.condition_on_previous_text,
                segment_on_silence=self._settings.segment_on_silence,
                min_silence_seconds=self._settings.min_silence_seconds,
                min_segment_seconds=self._settings.min_segment_seconds,
                max_segment_seconds=self._settings.max_segment_seconds,
                segment_padding_seconds=self._settings.segment_padding_seconds,
                vad_rms_threshold=self._settings.vad_rms_threshold,
                word_timestamps=self._settings.word_timestamps,
                hallucination_silence_threshold=self._settings.hallucination_silence_threshold,
                language_override=self._language_override,
            )
            original_text = text
            polished_text = ""
            if text and self._postprocess_enabled:
                try:
                    text = postprocess_text(text, self._build_postprocess_config())
                    polished_text = text
                except Exception as exc:
                    logger.error("Post-processing failed: %s", exc)
            json_path = write_transcript_json(
                audio_path,
                text,
                original_text=original_text,
                polished_text=polished_text,
            )
            logger.info("Saved transcript: %s", json_path)
            if text:
                paste_text(text)
        except Exception as exc:
            logger.error("Transcription failed: %s", exc)
        finally:
            self._decrement_transcribing()

    def run(self) -> None:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._controller = StatusBarController.alloc().initWithApp_(self)
        if self._pending_permission_notice and self._is_accessibility_trusted():
            self._clear_pending_permission_notice()
        try:
            self._hotkeys.start()
        except RuntimeError as exc:
            logging.getLogger(__name__).warning("%s", exc)
        if self._pending_permission_notice:
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                self._show_permission_notice
            )
        app.run()
