"""
Word Lookup Popup for Windows
=====================================
Mac-like "Look Up" feature for Windows with AI-powered definitions.

SETUP:
  pip install keyboard pyperclip requests

OLLAMA SETUP (optional, for AI definitions):
  - Install Ollama from https://ollama.ai/
  - Run: ollama pull llama3  (or your preferred model)
  - Start Ollama service

RUN (as Administrator - needed for global hotkeys):
  python word_lookup.py

HOTKEY:  Alt + Shift + D
  -> Select any word anywhere, press the hotkey, get the definition popup.
  -> Works with online dictionary API first, falls back to local Ollama AI.
  -> Works in Chrome PDFs, regular text, emails, etc.
  -> Press Esc or click outside to dismiss.
  -> Press Ctrl+C in this terminal to quit cleanly.

TROUBLESHOOTING:
  If popup doesn't appear in Chrome PDF:
  1. Make sure script is running as Administrator
  2. Try the ALTERNATIVE_HOTKEY (Ctrl+Alt+D) instead
  3. If still not working, edit ALTERNATIVE_HOTKEY below

  If Ollama definitions don't work:
  1. Make sure Ollama is running: ollama serve
  2. Check if model is installed: ollama list
  3. Verify localhost:11434 is accessible
"""

import signal
import time
import threading
import tkinter as tk
from tkinter import font as tkfont
import requests
import keyboard
import pyperclip
import ctypes
from ctypes import wintypes

# -----------------------------------------------------
# WINDOWS ACCESSIBILITY: Read selected text from Chrome
# -----------------------------------------------------

# Get IAccessible interface (Windows Accessibility API)
oleacc = ctypes.windll.oleacc
user32 = ctypes.windll.user32


def get_selected_text_from_window():
    """
    Try to get selected text directly from the focused window
    using Windows Accessibility API (works with Chrome PDF).
    """
    try:
        # Get the currently focused window
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        # Try to get the accessibility object
        p_acc = ctypes.POINTER(ctypes.c_void_p)()
        var_child = wintypes.VARIANT()

        result = oleacc.AccessibleObjectFromWindow(
            hwnd,
            ctypes.c_long(-4),  # OBJID_CARET
            ctypes.byref(ctypes.c_int()),
            ctypes.byref(p_acc),
        )

        if result == 0 and p_acc and var_child:
            # Placeholder for deeper accessibility parsing if needed later.
            return None

    except Exception:
        pass

    return None


def get_selected_text_retry(max_attempts=3, poll_interval=0.05):
    """
    Get selected text with multiple retry attempts and frequent polling for faster response.
    """
    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                time.sleep(0.4)
            old = pyperclip.paste()
            pyperclip.copy("")
            keyboard.press_and_release("ctrl+c")
            # Poll clipboard for up to 0.5s, 0.7s, 0.9s
            max_wait = 0.5 + (attempt * 0.2)
            waited = 0.0
            word = ""
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
            if attempt < max_attempts - 1:
                print(f"[RETRY] Clipboard empty, retrying in {max_wait:.1f}s...")
        except Exception as exc:
            print(f"[ERROR on retry {attempt + 1}] {exc}")
    return None


# -----------------------------------------------------
# HOTKEYS
# -----------------------------------------------------
HOTKEY = "alt+shift+d"
ALTERNATIVE_HOTKEY = "ctrl+alt+d"  # Try this if main hotkey doesn't work in Chrome


# -----------------------------------------------------
# API - Free Dictionary API (no key needed)
# -----------------------------------------------------

# Use a global requests.Session for connection reuse
_session = requests.Session()


def fetch_definition(word: str):
    word = word.strip().lower()
    if not word or len(word) > 60:
        return None
    try:
        resp = _session.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}",
            timeout=3,
        )
        if resp.status_code == 200:
            return resp.json()[0]
    except Exception:
        pass
    return None


def fetch_ollama_definition(word: str):
    """
    Fetch sentence definition from local Ollama instance.
    Returns a formatted definition that can be used as fallback or alternative.
    """
    word = word.strip().lower()
    if not word or len(word) > 60:
        return None

    try:
        response = _session.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3",
                "prompt": (
                    f"Define the word '{word}' in one clear, concise sentence. "
                    "Focus on the most common meaning."
                ),
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "max_tokens": 100,
                },
            },
            timeout=15,
        )
        if response.status_code == 200:
            result = response.json()
            definition = result.get("response", "").strip()
            if definition:
                definition = definition.strip("\"'").strip()
                if not definition.endswith((".", "!", "?")):
                    definition += "."
                return {
                    "word": word.title(),
                    "meanings": [
                        {
                            "partOfSpeech": "AI Definition",
                            "definitions": [
                                {
                                    "definition": definition,
                                    "example": None,
                                }
                            ],
                        }
                    ],
                }
    except Exception as exc:
        print(f"[OLLAMA ERROR] Could not connect to Ollama: {exc}")
        print("[OLLAMA INFO] Make sure Ollama is running on localhost:11434")

    return None


def fetch_ollama_phrase_explanation(phrase: str):
    """
    Fetch plain English explanation for phrases from local Ollama instance.
    Used for multi-word phrases that exceed the normal word lookup limits.
    """
    phrase = phrase.strip()
    if not phrase:
        return None

    try:
        response = _session.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3",
                "prompt": (
                    "Explain this phrase in plain, simple English: "
                    f"'{phrase}'. Focus on making it easy to understand "
                    "for someone unfamiliar with the concept."
                ),
                "stream": False,
                "options": {
                    "temperature": 0.4,
                    "max_tokens": 150,
                },
            },
            timeout=25,
        )
        if response.status_code == 200:
            result = response.json()
            explanation = result.get("response", "").strip()
            if explanation:
                explanation = explanation.strip("\"'").strip()
                if not explanation.endswith((".", "!", "?")):
                    explanation += "."
                return {
                    "word": phrase[:50] + "..." if len(phrase) > 50 else phrase,
                    "meanings": [
                        {
                            "partOfSpeech": "Plain English Explanation",
                            "definitions": [
                                {
                                    "definition": explanation,
                                    "example": None,
                                }
                            ],
                        }
                    ],
                }
    except Exception as exc:
        print(f"[OLLAMA ERROR] Could not connect to Ollama for phrase explanation: {exc}")
        print("[OLLAMA INFO] Make sure Ollama is running on localhost:11434")

    return None


# -----------------------------------------------------
# COLORS - macOS-style glassy popup
# -----------------------------------------------------
BG = "#2a2a2e"
GLASS_ALPHA = 1
ACCENT = "#0a84ff"
TEXT = "#f5f5f7"
TEXT_DIM = "#a1a1a6"
POS_COL = "#55b1e1"
EX_COL = "#726b62"
BORDER = "#ffffff"


class LookupPopup:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._win = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._title_frame = None
        self._is_dragging = False
        self._drag_start_win_x = 0
        self._drag_start_win_y = 0

    def show(self, word: str, mx: int, my: int):
        """Called from hotkey thread - schedules work on main thread."""
        threading.Thread(
            target=self._fetch_then_show,
            args=(word, mx, my),
            daemon=True,
        ).start()

    def _fetch_then_show(self, word, mx, my):
        self.root.after(0, self._close)

        is_phrase = len(word.split()) > 2 or len(word) > 60

        if is_phrase:
            data = fetch_ollama_phrase_explanation(word)
        else:
            data = fetch_definition(word)
            if data is None:
                print("[INFO] Dictionary API failed, trying Ollama...")
                data = fetch_ollama_definition(word)

        self.root.after(0, lambda: self._draw(word, data, mx, my))

    def _close(self):
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

    def _draw(self, word: str, data, mx: int, my: int):
        self._close()

        win = tk.Toplevel(self.root)
        self._win = win
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        win.attributes("-alpha", 0.0)

        container = tk.Frame(win, bg=BG)
        container.pack(fill="both", expand=True)

        title_bar = tk.Frame(container, bg=BG, height=20, cursor="hand2")
        title_bar.pack(fill="x", padx=0, pady=0)
        title_bar.pack_propagate(False)
        drag_indicator = tk.Frame(title_bar, bg=TEXT_DIM, height=2, width=30)
        drag_indicator.place(relx=0.5, rely=0.5, anchor="center")

        inner = tk.Frame(container, bg=BG, padx=20, pady=16)
        inner.pack(fill="both", expand=True)

        self._title_frame = title_bar
        title_bar.bind("<Button-1>", self._on_drag_start)
        inner.bind("<Button-1>", self._on_drag_start)
        win.bind("<Button-1>", self._on_drag_start)
        win.bind("<B1-Motion>", self._on_drag_motion)
        win.bind("<ButtonRelease-1>", self._on_drag_stop)

        f_word = tkfont.Font(family="Helvetica", size=18, weight="bold")
        f_phon = tkfont.Font(family="Helvetica", size=9, weight="normal")
        f_pos = tkfont.Font(family="Helvetica", size=9, slant="italic")
        f_def = tkfont.Font(family="Helvetica", size=10, weight="normal")
        f_ex = tkfont.Font(family="Helvetica", size=9, slant="italic")
        f_small = tkfont.Font(family="Helvetica", size=8, weight="normal")

        widgets = []
        if data is None:
            widgets.append(
                tk.Label(
                    inner,
                    text=f"\"{word.title()}\"",
                    font=f_word,
                    fg=ACCENT,
                    bg=BG,
                    anchor="w",
                    pady=8,
                )
            )
            widgets.append(
                tk.Label(
                    inner,
                    text="No definition found.",
                    font=f_def,
                    fg=TEXT_DIM,
                    bg=BG,
                    anchor="w",
                )
            )
        else:
            header = tk.Frame(inner, bg=BG)
            widgets.append(header)
            widgets.append(
                tk.Label(
                    header,
                    text=data.get("word", word).title(),
                    font=f_word,
                    fg=ACCENT,
                    bg=BG,
                    anchor="w",
                )
            )
            phon = data.get("phonetic", "")
            if phon:
                widgets.append(
                    tk.Label(
                        header,
                        text=f" {phon}",
                        font=f_phon,
                        fg=TEXT_DIM,
                        bg=BG,
                        anchor="w",
                    )
                )
            widgets.append(tk.Frame(inner, bg=BORDER, height=1))
            for i, meaning in enumerate(data.get("meanings", [])[:3]):
                if i > 0:
                    widgets.append(tk.Frame(inner, bg=BORDER, height=1))
                widgets.append(
                    tk.Label(
                        inner,
                        text=meaning.get("partOfSpeech", ""),
                        font=f_pos,
                        fg=POS_COL,
                        bg=BG,
                        anchor="w",
                        pady=6,
                    )
                )
                for d in meaning.get("definitions", [])[:2]:
                    row = tk.Frame(inner, bg=BG)
                    widgets.append(row)
                    widgets.append(
                        tk.Label(
                            row,
                            text="*",
                            font=f_def,
                            fg=TEXT_DIM,
                            bg=BG,
                            width=2,
                            anchor="nw",
                            padx=8,
                        )
                    )
                    widgets.append(
                        tk.Label(
                            row,
                            text=d.get("definition", ""),
                            font=f_def,
                            fg=TEXT,
                            bg=BG,
                            wraplength=360,
                            justify="left",
                            anchor="nw",
                        )
                    )
                    if d.get("example"):
                        ex_frame = tk.Frame(inner, bg=BG)
                        widgets.append(ex_frame)
                        widgets.append(
                            tk.Label(
                                ex_frame,
                                text=f"\"{d['example']}\"",
                                font=f_ex,
                                fg=EX_COL,
                                bg=BG,
                                wraplength=340,
                                justify="left",
                                anchor="w",
                            )
                        )
            syns = []
            for meaning in data.get("meanings", [])[:2]:
                for d in meaning.get("definitions", []):
                    syns.extend(d.get("synonyms", []))
            if syns:
                widgets.append(tk.Frame(inner, bg=BORDER, height=1))
                widgets.append(
                    tk.Label(
                        inner,
                        text="Synonyms:",
                        font=f_pos,
                        fg=POS_COL,
                        bg=BG,
                        anchor="w",
                        pady=4,
                    )
                )
                widgets.append(
                    tk.Label(
                        inner,
                        text=", ".join(syns[:8]),
                        font=f_small,
                        fg=TEXT_DIM,
                        bg=BG,
                        wraplength=380,
                        justify="left",
                        anchor="w",
                    )
                )
        widgets.append(
            tk.Label(
                inner,
                text="drag to move | press esc to close",
                font=f_small,
                fg=TEXT_DIM,
                bg=BG,
                anchor="e",
                pady=12,
            )
        )

        for widget in widgets:
            if isinstance(widget, tk.Frame):
                widget.pack(fill="x", pady=(6, 10))
            else:
                anchor = widget.cget("anchor") if "anchor" in widget.keys() else "w"
                widget.pack(anchor=anchor)

        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        pos_x = min(mx + 14, screen_w - width - 20)
        pos_y = my + 22 if my + 22 + height < screen_h - 40 else my - height - 10
        win.geometry(f"+{pos_x}+{pos_y}")

        win.deiconify()
        self._fade_in(win, alpha=0.0, target_alpha=GLASS_ALPHA, duration=150)
        win.bind("<Escape>", lambda _: self._close())
        win.bind("<FocusOut>", lambda _: self._close())
        win.focus_force()

    def _on_drag_start(self, event):
        if not self._win:
            return

        self._is_dragging = True
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root

        geom = self._win.geometry().split("+")
        if len(geom) >= 3:
            self._drag_start_win_x = int(geom[1])
            self._drag_start_win_y = int(geom[2])

        self._win.attributes("-alpha", 0.98)

    def _on_drag_motion(self, event):
        if not self._win or not self._is_dragging:
            return

        delta_x = event.x_root - self._drag_start_x
        delta_y = event.y_root - self._drag_start_y

        new_x = self._drag_start_win_x + delta_x
        new_y = self._drag_start_win_y + delta_y

        screen_width = self._win.winfo_screenwidth()
        screen_height = self._win.winfo_screenheight()
        window_width = self._win.winfo_width()
        window_height = self._win.winfo_height()

        new_x = max(0, min(new_x, screen_width - window_width))
        new_y = max(0, min(new_y, screen_height - window_height))

        self._win.geometry(f"+{new_x}+{new_y}")

    def _on_drag_stop(self, _event):
        if self._win and self._is_dragging:
            self._win.attributes("-alpha", GLASS_ALPHA)

        self._is_dragging = False

    def _fade_in(self, win, alpha=0.0, target_alpha=0.96, duration=150, steps=10):
        if alpha < target_alpha:
            alpha += (target_alpha - 0.0) / steps
            alpha = min(alpha, target_alpha)
            win.attributes("-alpha", alpha)
            step_delay = duration // steps
            self.root.after(
                step_delay,
                lambda: self._fade_in(win, alpha, target_alpha, duration, steps),
            )


def get_cursor_pos():
    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def on_hotkey():
    try:
        word = get_selected_text_retry(max_attempts=3)
        if not word:
            print("[WARNING] Could not capture text.")
            print("  - Is text selected?")
            print("  - Try clicking inside the PDF first")
            print("  - Make sure this script is running as Administrator")
            return

        mx, my = get_cursor_pos()

        if len(word.split()) > 2 or len(word) > 60:
            print(f"[INFO] Multi-word phrase detected: '{word}'")
            print("[INFO] Getting plain English explanation from Ollama...")
            print(f"[SHOW] Displaying explanation for: '{word[:30]}...'")
            popup.show(word, mx, my)
            return

        print(f"[SHOW] Displaying definition for: '{word}'")
        popup.show(word, mx, my)

    except Exception as exc:
        print(f"[ERROR] Hotkey handler: {exc}")


def main():
    root = tk.Tk()
    root.withdraw()

    popup = LookupPopup(root)

    keyboard.add_hotkey(HOTKEY, on_hotkey)
    keyboard.add_hotkey(ALTERNATIVE_HOTKEY, on_hotkey)

    def quit_app(*_):
        print("\n[Quitting...]")
        keyboard.unhook_all()
        root.after(0, root.quit)

    signal.signal(signal.SIGINT, quit_app)

    def _poll():
        root.after(200, _poll)

    _poll()

    print("========================================")
    print("  Word Lookup - running")
    print(f"  Hotkey #1  :  {HOTKEY.upper()}")
    print(f"  Hotkey #2  :  {ALTERNATIVE_HOTKEY.upper()}")
    print("  Dictionary :  Online API -> Local Ollama AI")
    print("")
    print("  HOW TO USE:")
    print("  1. In Chrome PDF: Click in the document first")
    print("  2. Select a word (double-click or drag)")
    print("  3. Press Alt+Shift+D (or Ctrl+Alt+D)")
    print("  4. Definition popup appears at cursor")
    print("")
    print("  If it doesn't work:")
    print("  - Try clicking the PDF again to focus it")
    print("  - Try selecting the word again")
    print("  - Check terminal output for troubleshooting")
    print("  - Make sure script runs as Administrator")
    print("  - For AI definitions: ensure Ollama is running")
    print("")
    print("  Ctrl+C here to quit")
    print("========================================")

    root.mainloop()


if __name__ == "__main__":
    main()
