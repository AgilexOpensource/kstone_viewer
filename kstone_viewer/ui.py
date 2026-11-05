"""Shared viewer context: bilingual widgets, tabs, display toggles, notifications and sync guards.

Every label is registered as a ``(zh, en)`` pair so the language switch relabels the whole panel in place. Values
written from Python go through :meth:`ViewerUI.set`, which suppresses the resulting ``on_update`` callback on the
calling thread, so views never mistake their own sync-back for user input.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Callable, Iterator, Sequence

from . import html
from .text import LANGUAGES, TEXT, Prefs

LOGGER = logging.getLogger("kstone.viewer")
Text = tuple[str, str]

TABS: tuple[tuple[str, str, str, str], ...] = (
    ("overview", "概览", "Overview", "DASHBOARD"),
    ("tasks", "任务", "Tasks", "TARGET"),
    ("behaviors", "行为", "Behaviors", "SPARKLES"),
    ("chassis", "底盘", "Chassis", "CAR"),
    ("safety", "安全", "Safety", "SHIELD_CHECK"),
    ("plots", "曲线", "Plots", "CHART_LINE"),
    ("joints", "关节", "Joints", "ADJUSTMENTS_HORIZONTAL"),
    ("display", "显示", "Display", "SETTINGS"),
    ("extensions", "扩展", "Extensions", "PUZZLE"),
)

# Display keys double as preference keys.
DISPLAY_DEFAULTS: dict[str, bool] = {
    "show_grid": True, "show_robot": True, "show_measured": False, "show_skeleton": True, "show_gizmos": True,
    "show_planned": True, "show_labels": True, "chassis_vectors": True, "chassis_trail": True, "show_gaze": True,
    "show_com": True, "follow_camera": True, "dark_mode": False,
}
DISPLAY_LABELS: dict[str, Text] = {key: TEXT[key] for key in DISPLAY_DEFAULTS if key != "dark_mode"}


class Choice:
    """Dropdown or button group keyed by stable ids while showing translated labels."""

    def __init__(self, ui: "ViewerUI", handle: Any, options: Sequence[tuple[str, str, str]], key: str,
                 buttons: bool) -> None:
        self.ui, self.handle, self.options, self.buttons = ui, handle, tuple(options), buttons
        self._key = key
        self._callbacks: list[Callable[[str], None]] = []
        self.client: Any = None  # the client of the last button click (for per-client confirmations)
        if buttons:
            handle.on_click(self._clicked)
        else:
            handle.on_update(lambda _: self._picked(handle.value))

    def _clicked(self, event: Any) -> None:
        self.client = getattr(event, "client", None)
        self._picked(event.target.value)

    def labels(self) -> tuple[str, ...]:
        return tuple(self.ui.t(zh, en) for _, zh, en in self.options)

    def label(self, key: str) -> str:
        return next((self.ui.t(zh, en) for option, zh, en in self.options if option == key), key)

    def _picked(self, label: str) -> None:
        if not self.ui.user():
            return
        key = next((option for option, zh, en in self.options if label in (zh, en)), None)
        if key is None:
            return
        self._key = key
        for callback in self._callbacks:
            callback(key)

    @property
    def key(self) -> str:
        return self._key

    @key.setter
    def key(self, key: str) -> None:
        self._key = key
        if not self.buttons and self.handle.value != self.label(key):
            with self.ui.syncing():
                self.handle.value = self.label(key)

    def on_change(self, callback: Callable[[str], None]) -> None:
        self._callbacks.append(callback)

    def relabel(self) -> None:
        with self.ui.syncing():
            self.handle.options = self.labels()
            if not self.buttons:
                self.handle.value = self.label(self._key)


class ViewerUI:
    """Everything views share: the server, language, tabs, display toggles and host-thread notifications."""

    def __init__(self, server: Any, *, language: str | None = None, preferences: Prefs | None = None,
                 host: str = "127.0.0.1") -> None:
        self.server = server
        self.host = host
        self.gui = server.gui
        self.scene = server.scene
        self.preferences = preferences or Prefs(None)
        self.language = language or self.preferences.get("language", "zh", lambda v: v in LANGUAGES)
        if self.language not in LANGUAGES:
            self.language = "zh"
        self.display = {key: self.preferences.get(key, value) for key, value in DISPLAY_DEFAULTS.items()}
        self.display_labels: dict[str, Text] = dict(DISPLAY_LABELS)
        self.positions: dict[str, float] = {}
        self.tabs: dict[str, Any] = {}
        self.anchors: dict[str, Any] = {}
        self._bindings: list[tuple[Any, str, Text]] = []
        self._choices: list[Choice] = []
        self._display_listeners: list[Callable[[str, bool], None]] = []
        self._local = threading.local()
        self._notices: dict[str, float] = {}

    # -- language ------------------------------------------------------------------------------------------------
    def t(self, zh: str, en: str) -> str:
        return zh if self.language == "zh" else en

    def bind(self, handle: Any, attribute: str, text: Text) -> Any:
        self._bindings.append((handle, attribute, text))
        return handle

    def key(self, handle: Any, attribute: str, key: str, **fields: Any) -> Any:
        """Bind a ``text.TEXT`` entry, formatted with ``fields``."""
        return self.bind(handle, attribute, tuple(s.format(**fields) if fields else s for s in TEXT[key]))

    def k(self, key: str, **fields: Any) -> Text:
        """A ``text.TEXT`` entry as a (zh, en) pair, formatted with ``fields``."""
        return tuple(s.format(**fields) if fields else s for s in TEXT[key])

    def set_language(self, language: str) -> None:
        if language not in LANGUAGES or language == self.language:
            return
        self.language = language
        self.preferences.set("language", language)
        for handle, attribute, (zh, en) in self._bindings:
            try:
                if attribute == "tab":
                    handle._label = self.t(zh, en)  # viser has no public tab label setter
                    handle._parent._rebuild_tab_props()
                else:
                    setattr(handle, attribute, self.t(zh, en))
            except Exception as error:  # noqa: BLE001 - a stale handle must not block relabeling
                LOGGER.debug("relabel failed: %s", error)
        for choice in self._choices:
            choice.relabel()

    # -- display toggles -----------------------------------------------------------------------------------------
    def show(self, key: str) -> bool:
        return self.display.get(key, True)

    def declare_display(self, key: str, label: Text, default: bool = True) -> None:
        """Add a scene toggle ``key`` for an extension view (call in ``attach``; list it in ``View.display``)."""
        self.display_labels.setdefault(key, (str(label[0]), str(label[1])))
        self.display.setdefault(key, self.preferences.get(key, bool(default)))

    def set_display(self, key: str, value: bool) -> None:
        self.display[key] = bool(value)
        self.preferences.set(key, bool(value))
        for callback in self._display_listeners:
            callback(key, bool(value))

    def on_display(self, callback: Callable[[str, bool], None]) -> None:
        self._display_listeners.append(callback)

    # -- sync guards ---------------------------------------------------------------------------------------------
    @contextlib.contextmanager
    def syncing(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth = depth

    def user(self) -> bool:
        """True inside a callback caused by the browser, False while Python writes values on this thread."""
        return getattr(self._local, "depth", 0) == 0

    def set(self, handle: Any, attribute: str, value: Any) -> None:
        if getattr(handle, attribute) != value:
            with self.syncing():
                setattr(handle, attribute, value)

    def set_value(self, handle: Any, value: Any, tolerance: float = 0.0) -> None:
        current = handle.value
        if isinstance(value, float) and isinstance(current, (int, float)) and abs(float(current) - value) <= tolerance:
            return
        self.set(handle, "value", value)

    # -- widgets -------------------------------------------------------------------------------------------------
    def tab(self, key: str) -> Any:
        return self.tabs[key]

    def folder(self, zh: str, en: str, *, expand: bool = True) -> Any:
        return self.bind(self.gui.add_folder(self.t(zh, en), expand_by_default=expand), "label", (zh, en))

    def _hint(self, handle: Any, hint: Text | None) -> Any:
        if hint is not None:
            self.bind(handle, "hint", hint)
        return handle

    def slider(self, zh: str, en: str, low: float, high: float, step: float, value: float, *,
               hint: Text | None = None, disabled: bool = False) -> Any:
        value = min(max(float(value), low), high)
        handle = self.gui.add_slider(self.t(zh, en), low, high, step, value, disabled=disabled,
                                     hint=None if hint is None else self.t(*hint))
        return self._hint(self.bind(handle, "label", (zh, en)), hint)

    def number(self, zh: str, en: str, value: float, *, low: float | None = None, high: float | None = None,
               step: float | None = None, hint: Text | None = None) -> Any:
        handle = self.gui.add_number(self.t(zh, en), value, min=low, max=high, step=step,
                                     hint=None if hint is None else self.t(*hint))
        return self._hint(self.bind(handle, "label", (zh, en)), hint)

    def checkbox(self, zh: str, en: str, value: bool, *, hint: Text | None = None, disabled: bool = False) -> Any:
        handle = self.gui.add_checkbox(self.t(zh, en), bool(value), disabled=disabled,
                                       hint=None if hint is None else self.t(*hint))
        return self._hint(self.bind(handle, "label", (zh, en)), hint)

    def button(self, zh: str, en: str, *, icon: str | None = None, color: str | None = None,
               hint: Text | None = None, disabled: bool = False, visible: bool = True) -> Any:
        handle = self.gui.add_button(self.t(zh, en), icon=icon_of(icon), color=color, disabled=disabled,
                                     visible=visible, hint=None if hint is None else self.t(*hint))
        return self._hint(self.bind(handle, "label", (zh, en)), hint)

    def text(self, zh: str, en: str, value: str, *, hint: Text | None = None) -> Any:
        handle = self.gui.add_text(self.t(zh, en), value, hint=None if hint is None else self.t(*hint))
        return self._hint(self.bind(handle, "label", (zh, en)), hint)

    def vector3(self, zh: str, en: str, value: Sequence[float], *, step: float = 0.01,
                low: Sequence[float] | None = None, high: Sequence[float] | None = None,
                hint: Text | None = None) -> Any:
        handle = self.gui.add_vector3(self.t(zh, en), tuple(float(v) for v in value), step=step,
                                      min=None if low is None else tuple(low),
                                      max=None if high is None else tuple(high),
                                      hint=None if hint is None else self.t(*hint))
        return self._hint(self.bind(handle, "label", (zh, en)), hint)

    def choice(self, zh: str, en: str, options: Sequence[tuple[str, str, str]], key: str, *,
               buttons: bool = False, hint: Text | None = None) -> Choice:
        labels = tuple(self.t(o_zh, o_en) for _, o_zh, o_en in options)
        if buttons:
            handle = self.gui.add_button_group(self.t(zh, en), labels, hint=None if hint is None else self.t(*hint))
        else:
            current = next((self.t(o_zh, o_en) for option, o_zh, o_en in options if option == key), labels[0])
            handle = self.gui.add_dropdown(self.t(zh, en), labels, initial_value=current,
                                           hint=None if hint is None else self.t(*hint))
        self._hint(self.bind(handle, "label", (zh, en)), hint)
        choice = Choice(self, handle, options, key, buttons)
        self._choices.append(choice)
        return choice

    def html(self, content: str = "") -> Any:
        return self.gui.add_html(content)

    def static(self, zh: str, en: str) -> Any:
        """Static bilingual HTML (help text); relabeled on language change."""
        return self.bind(self.gui.add_html(self.t(zh, en)), "content", (zh, en))

    # -- notifications -------------------------------------------------------------------------------------------
    def confirm(self, client: Any, title: Text, body: Text, confirm: Text, on_confirm: Callable[[], None], *,
                color: str = "orange") -> None:
        """Ask ``client`` (the one that clicked) in a modal; ``on_confirm`` runs only on the confirm button.

        Without a client (scripted clicks) nothing is asked and nothing runs.
        """
        if client is None:
            return
        with client.gui.add_modal(self.t(*title)) as modal:
            client.gui.add_html(html.dialog(self.t(*title), self.t(*body)))

            def accept(_: Any) -> None:
                modal.close()
                on_confirm()

            client.gui.add_button(self.t(*confirm), color=color, icon=icon_of("RESTORE")).on_click(accept)
            client.gui.add_button(self.t("取消", "Cancel"), color="gray").on_click(lambda _: modal.close())

    def notify(self, title: Text, body: str, *, color: str = "red", seconds: float = 6.0,
               dedupe_s: float = 3.0) -> None:
        key = f"{title}|{body}"
        now = time.monotonic()
        if now - self._notices.get(key, -1e9) < dedupe_s:
            return
        self._notices[key] = now
        for client in tuple(self.server.get_clients().values()):
            try:
                client.add_notification(self.t(*title), body, auto_close_seconds=seconds, color=color,
                                        with_close_button=True)
            except Exception as error:  # noqa: BLE001 - disconnected clients are expected
                LOGGER.debug("notification failed: %s", error)


def icon_of(name: str | None) -> Any:
    if name is None:
        return None
    import viser
    return getattr(viser.Icon, name)
