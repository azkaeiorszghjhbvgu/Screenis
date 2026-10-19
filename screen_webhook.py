"""
Screen -> Discord Webhook sender.

A small local GUI app for your own PC. Enter your Discord webhook URL,
pick an interval (seconds, minutes or hours), click Start to send a
full-screen screenshot on that interval, and click
End to stop and close the program.

Screenshots are captured in memory and uploaded directly to the webhook.
Nothing is ever written to disk.
"""

import ctypes
import io
import json
import os
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

import requests
from PIL import ImageGrab

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}
UNIT_MAX = {"seconds": 59, "minutes": 59, "hours": 9999}
THEMES = {
    "Light": {"bg": "SystemButtonFace", "fg": "SystemButtonText", "field": "SystemWindow",
              "field_fg": "SystemWindowText", "muted": "#555"},
    "Dark": {"bg": "#202020", "fg": "#e0e0e0", "field": "#2d2d2d",
             "field_fg": "#e0e0e0", "muted": "#aaa"},
}

# Config file sits next to this script and stores the webhook URL and interval.
# When built as an .exe, __file__ is a temp folder, so use the .exe's folder instead.
_APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_APP_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(**values):
    # Merge so saving one setting doesn't wipe the others.
    config = load_config()
    config.update(values)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except Exception:
        pass


def load_webhook():
    return load_config().get("webhook_url", "")


def save_webhook(url):
    save_config(webhook_url=url)


def list_monitors():
    """Return {label: crop box} for each monitor, boxes relative to the all-screens grab."""
    user32 = ctypes.windll.user32
    # Pillow grabs in per-monitor-DPI-aware physical pixels; measure monitors the same way.
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-3))
    rects = []
    try:
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def cb(_mon, _hdc, r, _lp):
            rects.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom))
            return True

        user32.EnumDisplayMonitors(None, None, proc(cb), 0)
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # virtual screen origin
    finally:
        if old_ctx:
            user32.SetThreadDpiAwarenessContext(old_ctx)

    monitors = {}
    for i, (l, t, r, b) in enumerate(sorted(rects), start=1):
        primary = " (primary)" if (l, t) == (0, 0) else ""
        monitors[f"Monitor {i}: {r - l}x{b - t}{primary}"] = (l - vx, t - vy, r - vx, b - vy)
    return monitors


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Screenis - Discord Webhook Screenshot Sender")
        # Inside the .exe, bundled files are unpacked to sys._MEIPASS.
        icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico")
        try:
            self.root.iconbitmap(default=icon)
        except tk.TclError:
            pass  # missing icon file shouldn't stop the app
        self.root.geometry("560x250")
        self.root.resizable(False, False)

        self.running = False
        self._timer = None  # threading.Timer handle
        self._send_count = 0
        self._seq = 0  # running number sent with each screenshot (#1, #2, ...)

        # --- Tabs ---
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=6, pady=6)
        main = tk.Frame(notebook)
        config = tk.Frame(notebook)
        notebook.add(main, text="Main")
        notebook.add(config, text="Config")

        # --- Always-on-top toggle ---
        self.ontop_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            config, text="Always on top", variable=self.ontop_var,
            command=self.toggle_ontop,
        ).pack(anchor="w", padx=12, pady=(14, 0))
        self.toggle_ontop()  # apply the default (on) at startup

        # --- Webhook URL input ---
        tk.Label(main, text="Discord Webhook URL:").pack(anchor="w", padx=12, pady=(10, 2))
        self.url_var = tk.StringVar(value=load_webhook())
        self.url_entry = tk.Entry(main, textvariable=self.url_var, width=60)
        self.url_entry.pack(padx=12)

        # --- Interval ---
        interval_frame = tk.Frame(main)
        interval_frame.pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(interval_frame, text="Send every:").pack(side="left")
        # One spinbox per unit: hour(s), minute(s), second(s).
        self.interval_vars = {}
        self.interval_spins = []
        saved = load_config().get("interval", {})
        for unit, label in (("hours", "hour(s)"), ("minutes", "minute(s)"), ("seconds", "second(s)")):
            var = tk.StringVar(value=str(saved.get(unit, 0)))
            spin = tk.Spinbox(interval_frame, from_=0, to=UNIT_MAX[unit], width=4, textvariable=var)
            spin.pack(side="left", padx=(6, 2))
            tk.Label(interval_frame, text=label).pack(side="left")
            self.interval_vars[unit] = var
            self.interval_spins.append(spin)

        # --- Display ---
        display_frame = tk.Frame(config)
        display_frame.pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(display_frame, text="Display:").pack(side="left")
        try:
            self.monitors = {"All monitors": None, **list_monitors()}
        except Exception:
            self.monitors = {"All monitors": None}  # enumeration failed; fall back to full grab
        self.display_var = tk.StringVar(value="All monitors")
        self.display_menu = tk.OptionMenu(display_frame, self.display_var, *self.monitors)
        self.display_menu.pack(side="left", padx=6)

        # --- Theme ---
        theme_frame = tk.Frame(config)
        theme_frame.pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(theme_frame, text="Theme:").pack(side="left")
        saved_theme = load_config().get("theme")
        self.theme_var = tk.StringVar(value=saved_theme if saved_theme in THEMES else "Light")
        tk.OptionMenu(theme_frame, self.theme_var, *THEMES,
                      command=lambda _: self.set_theme()).pack(side="left", padx=6)

        # --- Buttons ---
        btn_frame = tk.Frame(main)
        btn_frame.pack(pady=16)

        self.start_btn = tk.Button(
            btn_frame, text="Start", width=14, height=2,
            command=self.start,
        )
        self.start_btn.grid(row=0, column=0, padx=8)

        self.end_btn = tk.Button(
            btn_frame, text="End", width=14, height=2,
            command=self.end,
        )
        self.end_btn.grid(row=0, column=1, padx=8)

        self.test_btn = tk.Button(
            btn_frame, text="Test Webhook", width=14, height=2,
            command=self.test,
        )
        self.test_btn.grid(row=0, column=2, padx=8)

        # --- Status line ---
        self.status_var = tk.StringVar(value="Idle. Paste your webhook URL and click Start.")
        self.status_label = tk.Label(main, textvariable=self.status_var, wraplength=430,
                                     justify="left")
        self.status_label.pack(padx=12, pady=(0, 8))

        self._ttk_default = ttk.Style().theme_use()
        self.apply_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.end)

    def start(self):
        url = self.url_var.get().strip()
        if not url.startswith("https://"):
            messagebox.showerror("Invalid URL", "Please paste a valid Discord webhook URL (starts with https://).")
            return
        try:
            amounts = {u: int(v.get() or 0) for u, v in self.interval_vars.items()}
            if any(not 0 <= n <= UNIT_MAX[u] for u, n in amounts.items()):
                raise ValueError
            self._interval = sum(n * UNIT_SECONDS[u] for u, n in amounts.items())
            if self._interval < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid interval", "Please enter whole numbers (minutes and seconds 0-59) adding up to at least 1 second.")
            return
        save_config(webhook_url=url, interval=amounts)

        self.running = True
        self._send_count = 0
        self._seq = 0
        self._start_time = time.monotonic()
        self.start_btn.config(state="disabled")
        self.url_entry.config(state="disabled")
        for spin in self.interval_spins:
            spin.config(state="disabled")
        self.display_menu.config(state="disabled")
        self._status_prefix = "Sending first screenshot..."
        self._clock()
        # Send one immediately, then schedule at the chosen interval.
        self._tick()

    def set_theme(self):
        save_config(theme=self.theme_var.get())
        self.apply_theme()
        # Windows only repaints the title bar on re-show, so hide and show once.
        self.root.withdraw()
        self.root.deiconify()

    def apply_theme(self):
        t = THEMES[self.theme_var.get()]
        # Native Windows ttk theme ignores tab colors, so dark mode switches to "clam".
        style = ttk.Style()
        if self.theme_var.get() == "Dark":
            style.theme_use("clam")
            style.configure("TNotebook", background=t["bg"], borderwidth=0)
            style.configure("TNotebook.Tab", background=t["field"], foreground=t["fg"])
            style.map("TNotebook.Tab", background=[("selected", t["bg"])])
        else:
            style.theme_use(self._ttk_default)

        # Title bar: DWMWA_USE_IMMERSIVE_DARK_MODE (20) on Windows 10 2004+ / 11.
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            dark = ctypes.c_int(self.theme_var.get() == "Dark")
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        except Exception:
            pass  # older Windows: title bar just stays light

        widgets = [self.root]
        while widgets:
            w = widgets.pop()
            widgets.extend(w.winfo_children())
            cls = w.winfo_class()
            if cls in ("Tk", "Frame"):
                w.config(bg=t["bg"])
            elif cls == "Label":
                w.config(bg=t["bg"], fg=t["muted"] if w is self.status_label else t["fg"])
            elif cls == "Checkbutton":
                w.config(bg=t["bg"], fg=t["fg"], activebackground=t["bg"],
                         activeforeground=t["fg"], selectcolor=t["field"])
            elif cls in ("Entry", "Spinbox"):
                w.config(bg=t["field"], fg=t["field_fg"], insertbackground=t["field_fg"],
                         disabledbackground=t["bg"], readonlybackground=t["bg"])
                if cls == "Spinbox":
                    w.config(buttonbackground=t["bg"])
            elif cls == "Menubutton":  # OptionMenu
                w.config(bg=t["bg"], fg=t["fg"], activebackground=t["field"],
                         activeforeground=t["fg"], highlightbackground=t["bg"])
                w.nametowidget(w["menu"]).config(bg=t["field"], fg=t["field_fg"])
            elif cls == "Button":
                w.config(bg=t["field"], fg=t["fg"], activebackground=t["bg"],
                         activeforeground=t["fg"], disabledforeground=t["muted"])

    def toggle_ontop(self):
        self.root.attributes("-topmost", self.ontop_var.get())

    def test(self):
        # Send ONE screenshot with the message "Works!" without starting the loop.
        url = self.url_var.get().strip()
        if not url.startswith("https://"):
            messagebox.showerror("Invalid URL", "Please paste a valid Discord webhook URL (starts with https://).")
            return
        if self.running:
            return
        save_webhook(url)
        self.status_var.set("Sending test screenshot...")
        threading.Thread(
            target=self._capture_and_send,
            kwargs={"content": "Works!", "is_test": True},
            daemon=True,
        ).start()

    def _tick(self):
        if not self.running:
            return
        # Number each send: #1, #2, #3, ...
        self._seq += 1
        elapsed = int(time.monotonic() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        content = f"#{self._seq}\nRunning for {h}:{m:02d}:{s:02d}"
        # Capture + send on a background thread so the UI stays responsive.
        threading.Thread(
            target=self._capture_and_send,
            kwargs={"content": content},
            daemon=True,
        ).start()
        # Schedule the next run.
        self._timer = threading.Timer(self._interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _grab_excluding_self(self):
        # Hide our own window, grab the screen, then show the window again.
        # Runs the tkinter calls on the main thread; the worker waits for the result.
        holder = {}
        done = threading.Event()

        def do():
            try:
                self.root.withdraw()
                self.root.update()
                time.sleep(0.15)  # let the window actually disappear before grabbing
                img = ImageGrab.grab(all_screens=True)
                box = self.monitors[self.display_var.get()]
                holder["img"] = img.crop(box) if box else img
            except Exception as e:
                holder["err"] = e
            finally:
                self.root.deiconify()
                self.toggle_ontop()  # restore always-on-top state
                done.set()

        self.root.after(0, do)
        done.wait(timeout=10)
        if "err" in holder:
            raise holder["err"]
        return holder["img"]

    def _capture_and_send(self, content=None, is_test=False):
        try:
            # Grab the full screen into memory, excluding this app's window.
            img = self._grab_excluding_self()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)

            data = {"content": content} if content else None
            resp = requests.post(
                self.url_var.get().strip(),
                data=data,
                files={"file": ("screenshot.png", buf, "image/png")},
                timeout=30,
            )
            resp.raise_for_status()
            if is_test:
                self._set_status('Test sent: "Works!" with a screenshot. Click Start to begin.')
            else:
                self._send_count += 1
                self._status_prefix = f"Sent {self._send_count} screenshot(s)."
                self.root.after(0, self._show_running)
        except Exception as e:
            if is_test:
                self._set_status(f"Test failed: {e}")
            else:
                self._status_prefix = f"Send failed: {e}"
                self.root.after(0, self._show_running)
        finally:
            # Make sure the in-memory buffer is released.
            try:
                buf.close()
            except Exception:
                pass

    def _show_running(self):
        h, rem = divmod(int(time.monotonic() - self._start_time), 3600)
        m, s = divmod(rem, 60)
        self.status_var.set(f"{self._status_prefix} Running for {h:02d}:{m:02d}:{s:02d}")

    def _clock(self):
        # Live elapsed-time readout, refreshed every second while running.
        if self.running:
            self._show_running()
            self.root.after(1000, self._clock)

    def _set_status(self, text):
        # Update the label safely from a worker thread.
        self.root.after(0, lambda: self.status_var.set(text))

    def end(self):
        self.running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self.root.destroy()


if __name__ == "__main__":
    # Own taskbar identity, so the taskbar shows our icon instead of Python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Screenis")
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
