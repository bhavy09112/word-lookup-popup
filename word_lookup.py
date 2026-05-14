"""
Word Lookup Popup for Windows  v4.0 — Liquid Glass Edition
============================================================
Mac-like "Look Up" feature for Windows with AI-powered definitions.
Now with TRUE Windows Acrylic blur, DWM rounded corners, and a
full liquid-glass UI inspired by Apple's latest design language.

NEW IN v4.0:
  - 🔊 Text-to-Speech pronunciation (press 'S' when popup is open)
  - 📜 Word history navigation (Ctrl+H to view history)
  - 🌐 Quick web search buttons (Google, Wikipedia, DuckDuckGo)
  - 📋 Copy definition to clipboard (Ctrl+C when popup is open)
  - ⭐ Favorite words (Ctrl+F to save, Ctrl+L to view favorites)

SETUP:
  pip install keyboard pyperclip requests PyQt6 PyQt-Fluent-Widgets pyttsx3

OLLAMA SETUP (optional, for AI definitions):
  - Install Ollama from https://ollama.ai/
  - Run: ollama pull llama3  (or your preferred model)
  - Start Ollama service

RUN (as Administrator — needed for global hotkeys):
  python word_lookup.py

HOTKEY:  Alt + Shift + D
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import threading
import webbrowser
import requests
import keyboard
import pyperclip
import ctypes
import math

from datetime import datetime
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

# Try to import TTS engine
try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    print("[INFO] pyttsx3 not installed. Text-to-speech disabled. Run: pip install pyttsx3")

_app_for_import = QtWidgets.QApplication.instance()
if _app_for_import is None:
    _app_for_import = QtWidgets.QApplication(sys.argv)

# Use QMainWindow as base - more stable than Fluent widgets which have compatibility issues
_FLUENT_BASE = QtWidgets.QMainWindow
print("[INFO] Using QMainWindow for stable compatibility")


# ─────────────────────────────────────────────────────
#  STORAGE PATHS
# ─────────────────────────────────────────────────────

DATA_DIR = Path.home() / ".word_lookup"
DATA_DIR.mkdir(exist_ok=True)
HISTORY_FILE = DATA_DIR / "history.json"
FAVORITES_FILE = DATA_DIR / "favorites.json"
MAX_HISTORY = 50


def load_json(path: Path) -> list:
    """Load JSON list from file, return empty list if not exists."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def save_json(path: Path, data: list) -> None:
    """Save JSON list to file."""
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[ERROR] Could not save {path}: {e}")


# ─────────────────────────────────────────────────────
#  TEXT-TO-SPEECH ENGINE
# ─────────────────────────────────────────────────────

class TTSEngine:
    """Thread-safe TTS wrapper using pyttsx3."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None and TTS_AVAILABLE:
                    cls._instance = super().__new__(cls)
                    cls._instance.engine = None
        return cls._instance

    def speak(self, text: str) -> None:
        if not TTS_AVAILABLE:
            return
        def _speak():
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", 150)
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                print(f"[TTS ERROR] {e}")
        threading.Thread(target=_speak, daemon=True).start()


# Global TTS instance
_tts = TTSEngine() if TTS_AVAILABLE else None


# ─────────────────────────────────────────────────────
#  WINDOWS CLIPBOARD / CURSOR HELPERS
# ─────────────────────────────────────────────────────

def get_cursor_pos():
    class PT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    pt = PT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def get_selected_text_retry(max_attempts=3, poll_interval=0.05):
    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                time.sleep(0.4)
            old = pyperclip.paste()
            pyperclip.copy("")
            keyboard.press_and_release("ctrl+c")
            max_wait  = 0.5 + (attempt * 0.2)
            waited    = 0
            word      = ""
            while waited < max_wait:
                word = pyperclip.paste().strip()
                if word:
                    break
                time.sleep(poll_interval)
                waited += poll_interval
            pyperclip.copy(old)
            if word:
                print(f"[SUCCESS] Captured on attempt {attempt + 1}: '{word}'")
                return word
            elif attempt < max_attempts - 1:
                print(f"[RETRY] Clipboard empty…")
        except Exception as e:
            print(f"[ERROR] {e}")
    return None


# ─────────────────────────────────────────────────────
#  HOTKEYS
# ─────────────────────────────────────────────────────

HOTKEY             = "alt+shift+d"
ALTERNATIVE_HOTKEY = "ctrl+alt+d"


# ─────────────────────────────────────────────────────
#  DICTIONARY / OLLAMA APIs
# ─────────────────────────────────────────────────────

_session = requests.Session()


def fetch_definition(word: str):
    word = word.strip().lower()
    if not word or len(word) > 60:
        return None
    try:
        r = _session.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}",
            timeout=3
        )
        if r.status_code == 200:
            return r.json()[0]
    except Exception:
        pass
    return None


def _ollama_call(prompt: str, max_tokens: int = 120) -> "str | None":
    try:
        r = _session.post(
            "http://localhost:11434/api/generate",
            json={
                "model":   "qwen2.5-coder",
                "prompt":  prompt,
                "stream":  False,
                "options": {"temperature": 0.3, "max_tokens": max_tokens},
            },
            timeout=40,
        )
        if r.status_code != 200:
            print(f"[OLLAMA] non-200 status: {r.status_code}")
            return None

        payload = r.json()
        text = None

        if isinstance(payload, dict):
            if "response" in payload:
                text = payload.get("response")
            elif "choices" in payload:
                choices = payload.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    text = choices[0].get("content") or choices[0].get("text")
                    if not text and isinstance(choices[0].get("message"), dict):
                        text = choices[0]["message"].get("content") or choices[0]["message"].get("text")
            elif "output" in payload:
                output = payload.get("output")
                if isinstance(output, list) and output:
                    first = output[0]
                    if isinstance(first, dict):
                        text = first.get("content") or first.get("text")
                    elif isinstance(first, str):
                        text = first
            elif "result" in payload:
                result = payload.get("result")
                if isinstance(result, dict):
                    if "output" in result:
                        out = result.get("output")
                        if isinstance(out, list) and out:
                            first = out[0]
                            if isinstance(first, dict):
                                text = first.get("content") or first.get("text")
                            elif isinstance(first, str):
                                text = first
                    elif "choices" in result:
                        choices = result.get("choices") or []
                        if choices and isinstance(choices[0], dict):
                            text = choices[0].get("content") or choices[0].get("text")

        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                text = first.get("response") or first.get("content") or first.get("text")

        if isinstance(text, str):
            text = text.strip().strip('"\'')
            if text and not text[-1] in '.!?':
                text += '.'
            return text
        print(f"[OLLAMA] unexpected response format: {payload}")
    except Exception as e:
        print(f"[OLLAMA] {e}")
    return None


def fetch_ollama_definition(word: str):
    defn = _ollama_call(
        f"Define the word '{word}' in one clear, concise sentence. "
        "Focus on the most common meaning."
    )
    if defn:
        return {
            "word":     word.title(),
            "meanings": [{"partOfSpeech": "AI Definition",
                          "definitions":  [{"definition": defn, "example": None}]}],
        }
    return None


def fetch_ollama_phrase_explanation(phrase: str):
    expl = _ollama_call(
        f"Explain this phrase in plain, simple English: '{phrase}'. "
        "Make it easy to understand for someone unfamiliar with the concept.",
        max_tokens=160,
    )
    if expl:
        label = phrase[:50] + "…" if len(phrase) > 50 else phrase
        return {
            "word":     label,
            "meanings": [{"partOfSpeech": "Plain English",
                          "definitions":  [{"definition": expl, "example": None}]}],
        }
    return None


# ─────────────────────────────────────────────────────
#  LIQUID GLASS PALETTE
# ─────────────────────────────────────────────────────
# True liquid-glass / Apple Vision Pro–inspired colours.
# The DWM acrylic blur handles the actual frosted-glass backdrop;
# these colours live *on top* of it as the glass "tint" layer.

BG              = "#0e0e14"   # Deep dark base (real blurred bg shows through)
BG_ALPHA        = 0.95        # Front glass opacity
GLASS_ALPHA     = 0.95        # Window opacity — let acrylic blur shine
SPECULAR_TOP    = "#1a1a21"   # Slightly lighter for glass depth illusion
BORDER_LIGHT    = "#3a3a50"   # Subtle rim light
ACCENT          = "#3d9aff"   # iOS 18 system blue
ACCENT_GLOW     = "#1a6bdf"   # Deeper blue for glow layers
TEXT            = "#f0f0f5"   # Near-white body
TEXT_DIM        = "#8e8ea0"   # Muted secondary
POS_NOUN        = "#5ee7b0"   # Mint green for nouns
POS_VERB        = "#ff9f5a"   # Warm orange for verbs
POS_ADJ         = "#c084fc"   # Lavender for adjectives
POS_DEFAULT     = "#60c3ff"   # Cool blue default
EX_COL          = "#a6a6c4"   # Dim italic example
SEP_COL         = "#25253a"   # Separator line
PILL_COL        = "#404058"   # Drag-pill colour
HINT_COL        = "#50506a"   # Keyboard-hint text
SHIMMER_COL     = "#ffffff18" # Specular shimmer strip
BTN_SPEAK       = "#4ade80"   # Green for speak button
BTN_COPY        = "#60c3ff"   # Blue for copy
BTN_WEB         = "#f59e0b"   # Orange for web search
BTN_FAV         = "#f472b6"   # Pink for favorite
BTN_HIST        = "#a78bfa"   # Purple for history

# Part-of-speech → colour map
POS_COLORS = {
    "noun":         POS_NOUN,
    "verb":         POS_VERB,
    "adjective":    POS_ADJ,
    "adverb":       POS_ADJ,
    "pronoun":      POS_DEFAULT,
    "preposition":  POS_DEFAULT,
    "conjunction":  POS_DEFAULT,
    "interjection": POS_VERB,
    "ai definition": ACCENT,
    "plain english": POS_NOUN,
}

CORNER_RADIUS = 35


def pos_color(pos_str: str) -> str:
    return POS_COLORS.get(pos_str.lower(), POS_DEFAULT)


# ─────────────────────────────────────────────────────
#  COLOR HELPERS
# ─────────────────────────────────────────────────────

def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _qcolor(hex_color: str) -> QtGui.QColor:
    h = hex_color.lstrip("#")
    if len(h) == 8:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        a = int(h[6:8], 16)
        return QtGui.QColor(r, g, b, a)
    r, g, b = hex_to_rgb(hex_color)
    return QtGui.QColor(r, g, b)


# ─────────────────────────────────────────────────────
#  MAIN POPUP CLASS (PyQt6 + PyQt-Fluent-Widgets)
# ─────────────────────────────────────────────────────

class LookupPopup(_FLUENT_BASE):
    _MAX_W = 420

    drawRequested = QtCore.pyqtSignal(object, object, int, int)
    closeRequested = QtCore.pyqtSignal()
    speakRequested = QtCore.pyqtSignal(str)
    copyRequested = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()

        # Hide all Fluent window chrome - do this BEFORE setting window flags
        self._hide_fluent_chrome()

        if hasattr(self, "setMicaEffectEnabled"):
            try:
                self.setMicaEffectEnabled(True)
            except Exception:
                pass
        if hasattr(self, "windowEffect") and hasattr(self.windowEffect, "setAcrylicEffect"):
            try:
                self.windowEffect.setAcrylicEffect(self.winId())
            except Exception:
                pass

        self._is_dragging = False
        self._drag_pos = QtCore.QPoint()
        self._fade_anim = None
        self._scroll = None
        self._content = None
        self._rim_phase = 0.0
        self._rim_timer = QtCore.QTimer(self)
        self._rim_timer.timeout.connect(self._animate_rim)
        self._rim_timer.start(30)

        # Track current word for actions
        self._current_word = ""
        self._current_definition = None
        self._history = load_json(HISTORY_FILE)
        self._favorites = load_json(FAVORITES_FILE)

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.Tool |
            QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")

        self.drawRequested.connect(self._draw)
        self.closeRequested.connect(self._close)
        self.speakRequested.connect(self._speak_word)
        self.copyRequested.connect(self._copy_definition)

        self._build_base()

    def _hide_fluent_chrome(self):
        """Hide all Fluent window chrome elements."""
        # Hide title bar
        if hasattr(self, "titleBar") and self.titleBar:
            try:
                self.titleBar.hide()
                self.titleBar.setFixedHeight(0)
            except Exception:
                pass

        # Hide navigation view if present
        if hasattr(self, "navigationInterface"):
            try:
                self.navigationInterface.hide()
            except Exception:
                pass

        # For MSFluentWindow
        if hasattr(self, "navigationBar"):
            try:
                self.navigationBar.hide()
                self.navigationBar.setFixedHeight(0)
            except Exception:
                pass

        # Try to find and hide any additional bars
        for attr_name in ["titleBar", "navigationBar", "navBar", "appBar"]:
            if hasattr(self, attr_name):
                try:
                    attr = getattr(self, attr_name)
                    if attr:
                        attr.hide()
                        if hasattr(attr, "setFixedHeight"):
                            attr.setFixedHeight(0)
                except Exception:
                    pass

    # ── Public API ──────────────────────────────────

    def show_popup(self, word: str, mx: int, my: int):
        threading.Thread(
            target=self._fetch_then_show,
            args=(word, mx, my),
            daemon=True,
        ).start()

    # ── Internal ────────────────────────────────────

    def _fetch_then_show(self, word, mx, my):
        self.closeRequested.emit()
        is_phrase = len(word.split()) > 2 or len(word) > 60
        if is_phrase:
            data = fetch_ollama_phrase_explanation(word)
        else:
            data = fetch_definition(word)
            if data is None:
                print("[INFO] API miss → trying Ollama…")
                data = fetch_ollama_definition(word)
        self.drawRequested.emit(word, data, mx, my)

    def _close(self):
        self.hide()

    # ── Window construction ─────────────────────────

    def _build_base(self):
        # For QMainWindow-based Fluent windows, we need to set a central widget
        if issubclass(_FLUENT_BASE, QtWidgets.QMainWindow):
            self._central = QtWidgets.QWidget(self)
            self._central.setStyleSheet("background: transparent;")
            self.setCentralWidget(self._central)
            # Clear any existing layout
            if self._central.layout():
                QtWidgets.QWidget().setLayout(self._central.layout())
            root = self._central
        else:
            # For widget-based classes, use self directly
            root = self

        # Clear any existing layout
        old_layout = root.layout()
        if old_layout:
            QtWidgets.QWidget().setLayout(old_layout)

        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._scroll = QtWidgets.QScrollArea(root)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(self._scroll)

    def _set_content(self, word: str, data):
        if self._content is not None:
            self._content.deleteLater()

        # Track current word for actions
        self._current_word = word
        self._current_definition = data

        content = QtWidgets.QWidget()
        content.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, False)
        v = QtWidgets.QVBoxLayout(content)
        v.setContentsMargins(22, 4, 22, 10)
        v.setSpacing(0)

        def F(size, weight=QtGui.QFont.Weight.Normal, italic=False, family=None):
            fam = family or "Segoe UI Variable Text"
            f = QtGui.QFont(fam, size)
            f.setWeight(weight)
            f.setItalic(italic)
            return f

        fWord = F(20, QtGui.QFont.Weight.Bold, False, "Segoe UI Variable Display")
        fPhon = F(9)
        fPos  = F(9, italic=True)
        fDef  = F(10)
        fEx   = F(9, italic=True)
        fSyn  = F(8)
        fHint = F(7)
        fBtn  = F(8)

        # Drag handle pill
        pill_row = QtWidgets.QHBoxLayout()
        pill_row.setContentsMargins(0, 8, 0, 0)
        pill_row.addStretch(1)
        pill = QtWidgets.QWidget()
        pill.setFixedSize(36, 4)
        pill.setStyleSheet(
            f"background-color: {PILL_COL}; border-radius: 2px;"
        )
        pill_row.addWidget(pill)
        pill_row.addStretch(1)
        v.addLayout(pill_row)

        # Word + phonetic row
        word_row = QtWidgets.QHBoxLayout()
        word_row.setContentsMargins(0, 10, 0, 0)
        display_word = (data.get("word", word) if data else word).title()
        lbl_word = QtWidgets.QLabel(display_word)
        lbl_word.setFont(fWord)
        lbl_word.setStyleSheet(f"color: {ACCENT};")
        word_row.addWidget(lbl_word, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        phon = (data or {}).get("phonetic", "")
        if phon:
            lbl_phon = QtWidgets.QLabel(f" {phon}")
            lbl_phon.setFont(fPhon)
            lbl_phon.setStyleSheet(f"color: {TEXT_DIM};")
            word_row.addWidget(lbl_phon, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        word_row.addStretch(1)

        # Favorite star button
        is_fav = self._is_favorite(word)
        btn_fav = QtWidgets.QPushButton("★" if is_fav else "☆")
        btn_fav.setFont(F(14))
        btn_fav.setFixedSize(28, 28)
        btn_fav.setStyleSheet(
            f"QPushButton {{ border: none; color: {BTN_FAV if is_fav else TEXT_DIM}; background: transparent; }}"
            f"QPushButton:hover {{ color: {BTN_FAV}; }}"
        )
        btn_fav.clicked.connect(lambda: self._toggle_favorite(word))
        btn_fav.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        word_row.addWidget(btn_fav)

        v.addLayout(word_row)

        # ── Action Buttons Row ───────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 6)
        btn_row.setSpacing(8)

        # Speak button
        if TTS_AVAILABLE:
            btn_speak = QtWidgets.QPushButton("🔊 Speak")
            btn_speak.setFont(fBtn)
            btn_speak.setStyleSheet(
                f"QPushButton {{ background-color: {self._darker(BTN_SPEAK, 0.25)}; "
                f"color: {BTN_SPEAK}; border: none; border-radius: 10px; padding: 4px 10px; }}"
                f"QPushButton:hover {{ background-color: {self._darker(BTN_SPEAK, 0.35)}; }}"
            )
            btn_speak.clicked.connect(lambda: self.speakRequested.emit(word))
            btn_speak.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn_row.addWidget(btn_speak)

        # Copy button
        btn_copy = QtWidgets.QPushButton("📋 Copy")
        btn_copy.setFont(fBtn)
        btn_copy.setStyleSheet(
            f"QPushButton {{ background-color: {self._darker(BTN_COPY, 0.25)}; "
            f"color: {BTN_COPY}; border: none; border-radius: 10px; padding: 4px 10px; }}"
            f"QPushButton:hover {{ background-color: {self._darker(BTN_COPY, 0.35)}; }}"
        )
        btn_copy.clicked.connect(lambda: self.copyRequested.emit(word))
        btn_copy.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(btn_copy)

        # Web search buttons
        for engine, icon, url in [
            ("Google", "🔍", f"https://www.google.com/search?q={word}+meaning"),
            ("Wikipedia", "📖", f"https://en.wikipedia.org/wiki/{word}"),
            ("DuckDuckGo", "🦆", f"https://duckduckgo.com/?q={word}+definition"),
        ]:
            btn = QtWidgets.QPushButton(f"{icon}")
            btn.setFont(fBtn)
            btn.setToolTip(f"Search on {engine}")
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {self._darker(BTN_WEB, 0.25)}; "
                f"color: {BTN_WEB}; border: none; border-radius: 10px; padding: 4px 8px; }}"
                f"QPushButton:hover {{ background-color: {self._darker(BTN_WEB, 0.35)}; }}"
            )
            btn.clicked.connect(lambda checked, u=url: self._open_web(u))
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn_row.addWidget(btn)

        btn_row.addStretch(1)
        v.addLayout(btn_row)

        self._sep(v)

        if data is None:
            lbl = QtWidgets.QLabel("No definition found.")
            lbl.setFont(fDef)
            lbl.setStyleSheet(f"color: {TEXT_DIM};")
            lbl.setWordWrap(True)
            lbl.setContentsMargins(0, 12, 0, 12)
            v.addWidget(lbl)
        else:
            for i, meaning in enumerate(data.get("meanings", [])[:3]):
                if i > 0:
                    self._sep(v)

                pos = meaning.get("partOfSpeech", "")
                p_col = pos_color(pos)
                badge_bg = self._darker(p_col, 0.18)

                badge = QtWidgets.QLabel(pos)
                badge.setFont(fPos)
                badge.setStyleSheet(
                    f"color: {p_col}; background-color: {badge_bg};"
                    "border-radius: 9px; padding: 2px 8px;"
                )
                badge_row = QtWidgets.QHBoxLayout()
                badge_row.setContentsMargins(0, 8, 0, 2)
                badge_row.addWidget(badge, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
                badge_row.addStretch(1)
                v.addLayout(badge_row)

                for d in meaning.get("definitions", [])[:2]:
                    defn_text = d.get("definition", "")
                    row = QtWidgets.QHBoxLayout()
                    row.setContentsMargins(0, 3, 0, 0)

                    dot = QtWidgets.QLabel("●")
                    dot.setFont(F(7))
                    dot.setStyleSheet(f"color: {p_col};")
                    dot.setFixedWidth(16)
                    dot.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
                    row.addWidget(dot)

                    lbl_def = QtWidgets.QLabel(defn_text)
                    lbl_def.setFont(fDef)
                    lbl_def.setStyleSheet(f"color: {TEXT};")
                    lbl_def.setWordWrap(True)
                    row.addWidget(lbl_def, 1)
                    v.addLayout(row)

                    if d.get("example"):
                        ex = QtWidgets.QLabel(f'"{d["example"]}"')
                        ex.setFont(fEx)
                        ex.setStyleSheet(f"color: {EX_COL};")
                        ex.setWordWrap(True)
                        ex.setContentsMargins(24, 2, 0, 0)
                        v.addWidget(ex)

            # synonyms
            syns = []
            for m in data.get("meanings", [])[:2]:
                for d in m.get("definitions", []):
                    syns.extend(d.get("synonyms", []))
            if syns:
                self._sep(v)
                syn_row = QtWidgets.QHBoxLayout()
                syn_row.setContentsMargins(0, 6, 0, 2)
                lbl_also = QtWidgets.QLabel("Also: ")
                lbl_also.setFont(F(8, italic=True))
                lbl_also.setStyleSheet(f"color: {TEXT_DIM};")
                syn_row.addWidget(lbl_also, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

                lbl_syn = QtWidgets.QLabel(", ".join(syns[:7]))
                lbl_syn.setFont(fSyn)
                lbl_syn.setStyleSheet(f"color: {ACCENT};")
                lbl_syn.setWordWrap(True)
                syn_row.addWidget(lbl_syn, 1)
                v.addLayout(syn_row)

        # bottom hint
        hint = QtWidgets.QLabel("esc close  ·  s speak  ·  ctrl+c copy  ·  drag to move")
        hint.setFont(fHint)
        hint.setStyleSheet(f"color: {HINT_COL};")
        hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        hint.setContentsMargins(0, 10, 0, 10)
        v.addWidget(hint)

        self._scroll.setWidget(content)
        self._content = content
        self._install_drag_filters(content)

    def _draw(self, word: str, data, mx: int, my: int):
        self._set_content(word, data)
        self._scroll.widget().adjustSize()

        # Add to history
        self._add_to_history(word)

        screen = QtGui.QGuiApplication.primaryScreen()
        sgeo = screen.availableGeometry() if screen else self.screen().availableGeometry()
        max_h = int(sgeo.height() * 0.6)
        content_h = self._scroll.widget().sizeHint().height() + 8
        self._scroll.setMaximumHeight(max_h)
        self._scroll.setMinimumHeight(min(content_h, max_h))

        self.setFixedWidth(self._MAX_W)
        self.adjustSize()
        W = self.width()
        H = self.height()

        px = min(mx + 14, sgeo.right() - W - 20)
        py = (my + 22
              if my + 22 + H < sgeo.bottom() - 40
              else my - H - 10)
        self.move(px, py)

        super().show()
        self.raise_()
        self.activateWindow()


    # ── Drag ────────────────────────────────────────

    def _install_drag_filters(self, widget: QtWidgets.QWidget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QtWidgets.QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                self._is_dragging = True
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif event.type() == QtCore.QEvent.Type.MouseMove:
            if self._is_dragging:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
        elif event.type() == QtCore.QEvent.Type.MouseButtonRelease:
            if self._is_dragging:
                self._is_dragging = False
        return super().eventFilter(obj, event)

    # ── Animation ───────────────────────────────────

    def _animate_rim(self):
        self._rim_phase = (self._rim_phase + 2.0) % 360.0
        self.update()

    # ── Helpers ─────────────────────────────────────

    def _sep(self, layout: QtWidgets.QVBoxLayout):
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {SEP_COL};")
        line.setContentsMargins(2, 8, 2, 0)
        layout.addWidget(line)

    @staticmethod
    def _darker(hex_col: str, factor: float) -> str:
        r, g, b = hex_to_rgb(hex_col)
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── Action Methods ────────────────────────────────

    def _speak_word(self, word: str):
        """Speak the word using TTS."""
        if _tts:
            print(f"[TTS] Speaking: {word}")
            _tts.speak(word)

    def _copy_definition(self, word: str):
        """Copy the definition to clipboard."""
        text = word
        if self._current_definition:
            parts = []
            for m in self._current_definition.get("meanings", [])[:3]:
                pos = m.get("partOfSpeech", "")
                for d in m.get("definitions", [])[:2]:
                    defn = d.get("definition", "")
                    if pos:
                        parts.append(f"({pos}) {defn}")
                    else:
                        parts.append(defn)
            if parts:
                text = f"{word}: {'; '.join(parts)}"
        pyperclip.copy(text)
        print(f"[CLIPBOARD] Copied: {text[:50]}...")

    def _open_web(self, url: str):
        """Open URL in default browser."""
        webbrowser.open(url)

    def _is_favorite(self, word: str) -> bool:
        """Check if word is in favorites."""
        word_lower = word.lower()
        return any(f.get("word", "").lower() == word_lower for f in self._favorites)

    def _toggle_favorite(self, word: str):
        """Toggle favorite status for word."""
        word_lower = word.lower()
        # Remove if exists
        for i, f in enumerate(self._favorites):
            if f.get("word", "").lower() == word_lower:
                self._favorites.pop(i)
                save_json(FAVORITES_FILE, self._favorites)
                print(f"[FAV] Removed: {word}")
                # Refresh display
                self._set_content(self._current_word, self._current_definition)
                return
        # Add if not exists
        self._favorites.insert(0, {
            "word": word,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep max 100 favorites
        self._favorites = self._favorites[:100]
        save_json(FAVORITES_FILE, self._favorites)
        print(f"[FAV] Added: {word}")
        # Refresh display
        self._set_content(self._current_word, self._current_definition)

    def _add_to_history(self, word: str):
        """Add word to history."""
        entry = {
            "word": word,
            "timestamp": datetime.now().isoformat(),
        }
        # Remove duplicates
        self._history = [h for h in self._history if h.get("word", "").lower() != word.lower()]
        self._history.insert(0, entry)
        self._history = self._history[:MAX_HISTORY]
        save_json(HISTORY_FILE, self._history)

    # ── Events ──────────────────────────────────────

    def focusOutEvent(self, event):
        self._close()
        return super().focusOutEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if key == QtCore.Qt.Key.Key_Escape:
            self._close()
            return
        elif key == QtCore.Qt.Key.Key_S and modifiers == QtCore.Qt.KeyboardModifier.NoModifier:
            # Speak word
            if self._current_word:
                self._speak_word(self._current_word)
            return
        elif key == QtCore.Qt.Key.Key_C and modifiers == QtCore.Qt.KeyboardModifier.ControlModifier:
            # Copy definition
            if self._current_word:
                self._copy_definition(self._current_word)
            return
        elif key == QtCore.Qt.Key.Key_F and modifiers == QtCore.Qt.KeyboardModifier.ControlModifier:
            # Toggle favorite
            if self._current_word:
                self._toggle_favorite(self._current_word)
            return

        return super().keyPressEvent(event)

    def paintEvent(self, event):
        # Call parent paintEvent first for Fluent widgets to render their background
        super().paintEvent(event)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)

        bg = _qcolor(BG)
        bg.setAlphaF(BG_ALPHA)
        painter.fillPath(path, bg)

        # Animated Google-like rim
        rim_rect = rect.adjusted(1, 1, -1, -1)
        rim_path = QtGui.QPainterPath()
        rim_path.addRoundedRect(rim_rect, CORNER_RADIUS - 1, CORNER_RADIUS - 1)
        gradient = QtGui.QConicalGradient(rim_rect.center(), self._rim_phase)
        gradient.setColorAt(0.00, QtGui.QColor(66, 133, 244, 180))   # Blue
        gradient.setColorAt(0.25, QtGui.QColor(219, 68, 55, 180))   # Red
        gradient.setColorAt(0.50, QtGui.QColor(244, 180, 0, 180))   # Yellow
        gradient.setColorAt(0.75, QtGui.QColor(15, 157, 88, 180))   # Green
        gradient.setColorAt(1.00, QtGui.QColor(66, 133, 244, 180))
        pen = QtGui.QPen(QtGui.QBrush(gradient), 2)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(rim_path)

        # Specular shimmer strip
        shimmer_h = 40
        strip_rect = QtCore.QRectF(rect.left() + 4, rect.top() + 4,
                                   rect.width() - 8, shimmer_h)
        strip_path = QtGui.QPainterPath()
        strip_path.addRoundedRect(strip_rect, CORNER_RADIUS - 10, CORNER_RADIUS - 10)
        grad = QtGui.QLinearGradient(0, strip_rect.top(), 0, strip_rect.bottom())
        shimmer = _qcolor(SHIMMER_COL)
        transparent = QtGui.QColor(shimmer)
        transparent.setAlpha(0)
        grad.setColorAt(0.0, shimmer)
        grad.setColorAt(1.0, transparent)
        painter.fillPath(strip_path, grad)


# ─────────────────────────────────────────────────────
#  HISTORY / FAVORITES POPUP
# ─────────────────────────────────────────────────────

class ListPopup(_FLUENT_BASE):
    """Popup for displaying history or favorites."""

    itemClicked = QtCore.pyqtSignal(str, int, int)  # word, mx, my

    def __init__(self, title: str = "History"):
        super().__init__()

        # Hide all Fluent window chrome
        self._hide_fluent_chrome()

        if hasattr(self, "setMicaEffectEnabled"):
            try:
                self.setMicaEffectEnabled(True)
            except Exception:
                pass
        if hasattr(self, "windowEffect") and hasattr(self.windowEffect, "setAcrylicEffect"):
            try:
                self.windowEffect.setAcrylicEffect(self.winId())
            except Exception:
                pass

        self._title = title
        self._items: list[dict] = []

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.Tool |
            QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")

        self._build_ui()

    def _hide_fluent_chrome(self):
        """Hide all Fluent window chrome elements."""
        if hasattr(self, "titleBar") and self.titleBar:
            try:
                self.titleBar.hide()
                self.titleBar.setFixedHeight(0)
            except Exception:
                pass
        if hasattr(self, "navigationInterface"):
            try:
                self.navigationInterface.hide()
            except Exception:
                pass
        for attr_name in ["titleBar", "navigationBar", "navBar", "appBar"]:
            if hasattr(self, attr_name):
                try:
                    attr = getattr(self, attr_name)
                    if attr:
                        attr.hide()
                        if hasattr(attr, "setFixedHeight"):
                            attr.setFixedHeight(0)
                except Exception:
                    pass

    def _build_ui(self):
        if issubclass(_FLUENT_BASE, QtWidgets.QMainWindow):
            central = QtWidgets.QWidget(self)
            central.setStyleSheet("background: transparent;")
            self.setCentralWidget(central)
            root = central
        else:
            root = self

        # Clear any existing layout
        old_layout = root.layout()
        if old_layout:
            QtWidgets.QWidget().setLayout(old_layout)

        self._main_layout = QtWidgets.QVBoxLayout(root)
        self._main_layout.setContentsMargins(16, 12, 16, 12)
        self._main_layout.setSpacing(6)

    def set_items(self, items: list[dict], mx: int, my: int):
        """Set items and show popup."""
        self._items = items

        # Clear existing widgets
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Title
        def F(size, weight=QtGui.QFont.Weight.Normal, italic=False):
            f = QtGui.QFont("Segoe UI Variable Text", size)
            f.setWeight(weight)
            f.setItalic(italic)
            return f

        title_lbl = QtWidgets.QLabel(f"📖 {self._title}")
        title_lbl.setFont(F(14, QtGui.QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {ACCENT};")
        self._main_layout.addWidget(title_lbl)

        if not items:
            empty = QtWidgets.QLabel("No items yet")
            empty.setFont(F(10))
            empty.setStyleSheet(f"color: {TEXT_DIM};")
            empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self._main_layout.addWidget(empty)
        else:
            for item in items[:20]:  # Show max 20 items
                word = item.get("word", "")
                btn = QtWidgets.QPushButton(word)
                btn.setFont(F(11))
                btn.setStyleSheet(
                    f"QPushButton {{ text-align: left; padding: 6px 10px; "
                    f"background-color: transparent; color: {TEXT}; border: none; border-radius: 6px; }}"
                    f"QPushButton:hover {{ background-color: {self._darker(ACCENT, 0.15)}; color: {ACCENT}; }}"
                )
                btn.clicked.connect(lambda checked, w=word: self._on_click(w))
                btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
                self._main_layout.addWidget(btn)

        # Hint
        hint = QtWidgets.QLabel("esc to close")
        hint.setFont(F(8))
        hint.setStyleSheet(f"color: {HINT_COL};")
        hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self._main_layout.addWidget(hint)

        self.adjustSize()
        self.setFixedWidth(280)

        # Position
        screen = QtGui.QGuiApplication.primaryScreen()
        sgeo = screen.availableGeometry() if screen else self.screen().availableGeometry()
        W, H = self.width(), self.height()
        px = min(mx, sgeo.right() - W - 20)
        py = (my + 22 if my + 22 + H < sgeo.bottom() - 40 else my - H - 10)
        self.move(px, py)

        super().show()
        self.raise_()
        self.activateWindow()

    @staticmethod
    def _darker(hex_col: str, factor: float) -> str:
        r, g, b = hex_to_rgb(hex_col)
        return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"

    def _on_click(self, word: str):
        mx, my = get_cursor_pos()
        self.itemClicked.emit(word, mx, my)
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.hide()
        super().focusOutEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 20, 20)

        bg = _qcolor(BG)
        bg.setAlphaF(BG_ALPHA)
        painter.fillPath(path, bg)


# ─────────────────────────────────────────────────────
#  HOTKEY HANDLER
# ─────────────────────────────────────────────────────

def on_hotkey():
    try:
        word = get_selected_text_retry(max_attempts=3)
        if not word:
            print("[WARNING] No text captured — is something selected?")
            return
        mx, my = get_cursor_pos()
        print(f"[SHOW] '{word[:40]}'")
        popup.show_popup(word, mx, my)
    except Exception as e:
        print(f"[ERROR] {e}")


# ─────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    popup = LookupPopup()
    history_popup = ListPopup("History")
    favorites_popup = ListPopup("Favorites")

    # Connect history/favorites popup clicks to main popup
    history_popup.itemClicked.connect(popup.show_popup)
    favorites_popup.itemClicked.connect(popup.show_popup)

    def show_history():
        mx, my = get_cursor_pos()
        history_popup.set_items(popup._history, mx, my)

    def show_favorites():
        mx, my = get_cursor_pos()
        favorites_popup.set_items(popup._favorites, mx, my)

    keyboard.add_hotkey(HOTKEY,             on_hotkey)
    keyboard.add_hotkey(ALTERNATIVE_HOTKEY, on_hotkey)
    keyboard.add_hotkey("ctrl+h",          show_history)
    keyboard.add_hotkey("ctrl+l",          show_favorites)
    keyboard.add_hotkey("ctrl+shift+h",    show_history)
    keyboard.add_hotkey("ctrl+shift+f",    show_favorites)

    def quit_app(*_):
        print("\n[Quitting…]")
        keyboard.unhook_all()
        popup.close()
        history_popup.close()
        favorites_popup.close()
        app.quit()

    signal.signal(signal.SIGINT, quit_app)

    _sig_timer = QtCore.QTimer()
    _sig_timer.timeout.connect(lambda: None)
    _sig_timer.start(200)

    print("=" * 52)
    print("  [*] Word Lookup v4 - Liquid Glass Edition")
    print(f"  Hotkey #1    : {HOTKEY.upper()}")
    print(f"  Hotkey #2    : {ALTERNATIVE_HOTKEY.upper()}")
    print("  Engine       : Online API > Local Ollama AI")
    print("  Blur         : PyQt-Fluent-Widgets Acrylic")
    print()
    print("  NEW FEATURES:")
    print("  [+] Press 'S' to hear pronunciation")
    print("  [+] Press Ctrl+C to copy definition")
    print("  [+] Click star to favorite word")
    print("  [+] Press Ctrl+F to favorite the current word")
    print("  [+] Press Ctrl+H to view lookup history")
    print("  [+] Press Ctrl+L to view favorites")
    print("  [+] Click buttons for web search")
    print()
    print("  1. Select any word / phrase")
    print("  2. Press Alt+Shift+D")
    print("  3. Frosted-glass popup appears")
    print()
    print("  Ctrl+C to quit")
    print("=" * 52)

    sys.exit(app.exec())
