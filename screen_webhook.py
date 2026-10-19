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
from tkinter import messagebox

import requests
from PIL import ImageGrab

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}

# Config file sits next to this script and stores only the webhook URL.
# When built as an .exe, __file__ is a temp folder, so use the .exe's folder instead.
_APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_APP_DIR, "config.json")


def load_webhook():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("webhook_url", "")
    except Exception:
        return ""


def save_webhook(url):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"webhook_url": url}, f)
    except Exception:
        pass


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
        self.root.geometry("560x240")
        self.root.resizable(False, False)

        self.running = False
        self._timer = None  # threading.Timer handle
        self._send_count = 0
        self._seq = 0  # running number sent with each screenshot (#1, #2, ...)

        # --- Always-on-top toggle ---
        self.ontop_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            root, text="Always on top", variable=self.ontop_var,
            command=self.toggle_ontop,
        ).pack(anchor="e", padx=12, pady=(6, 0))
        self.toggle_ontop()  # apply the default (on) at startup

        # --- Webhook URL input ---
        tk.Label(root, text="Discord Webhook URL:").pack(anchor="w", padx=12, pady=(14, 2))
        self.url_var = tk.StringVar(value=load_webhook())
        self.url_entry = tk.Entry(root, textvariable=self.url_var, width=60)
        self.url_entry.pack(padx=12)

        # --- Interval ---
        interval_frame = tk.Frame(root)
        interval_frame.pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(interval_frame, text="Send every:").pack(side="left")
        self.amount_var = tk.StringVar(value="0")
        self.amount_spin = tk.Spinbox(interval_frame, from_=0, to=9999, width=6,
                                      textvariable=self.amount_var)
        self.amount_spin.pack(side="left", padx=6)
        self.unit_var = tk.StringVar(value="minutes")
        self.unit_menu = tk.OptionMenu(interval_frame, self.unit_var, *UNIT_SECONDS)
        self.unit_menu.pack(side="left")

        # --- Display ---
        tk.Label(interval_frame, text="Display:").pack(side="left", padx=(16, 0))
        try:
            self.monitors = {"All monitors": None, **list_monitors()}
        except Exception:
            self.monitors = {"All monitors": None}  # enumeration failed; fall back to full grab
        self.display_var = tk.StringVar(value="All monitors")
        self.display_menu = tk.OptionMenu(interval_frame, self.display_var, *self.monitors)
        self.display_menu.pack(side="left", padx=6)

        # --- Buttons ---
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=16)

        self.start_btn = tk.Button(
            btn_frame, text="Start", width=14, height=2,
            bg="#2e7d32", fg="white", command=self.start,
        )
        self.start_btn.grid(row=0, column=0, padx=8)

        self.end_btn = tk.Button(
            btn_frame, text="End", width=14, height=2,
            bg="#c62828", fg="white", command=self.end,
        )
        self.end_btn.grid(row=0, column=1, padx=8)

        self.test_btn = tk.Button(
            btn_frame, text="Test Webhook", width=14, height=2,
            bg="#1565c0", fg="white", command=self.test,
        )
        self.test_btn.grid(row=0, column=2, padx=8)

        # --- Status line ---
        self.status_var = tk.StringVar(value="Idle. Paste your webhook URL and click Start.")
        tk.Label(root, textvariable=self.status_var, fg="#555", wraplength=430,
                 justify="left").pack(padx=12, pady=(0, 8))

        self.root.protocol("WM_DELETE_WINDOW", self.end)

    def start(self):
        url = self.url_var.get().strip()
        if not url.startswith("https://"):
            messagebox.showerror("Invalid URL", "Please paste a valid Discord webhook URL (starts with https://).")
            return
        try:
            amount = int(self.amount_var.get())
            if amount < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid interval", "Please enter a whole number of 1 or more.")
            return
        self._interval = amount * UNIT_SECONDS[self.unit_var.get()]
        self._interval_text = f"{amount} {self.unit_var.get()}"
        save_webhook(url)

        self.running = True
        self._send_count = 0
        self._seq = 0
        self._start_time = time.monotonic()
        self.start_btn.config(state="disabled")
        self.url_entry.config(state="disabled")
        self.amount_spin.config(state="disabled")
        self.unit_menu.config(state="disabled")
        self.display_menu.config(state="disabled")
        self.status_var.set("Started. Sending first screenshot...")
        # Send one immediately, then schedule at the chosen interval.
        self._tick()

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
                self._set_status(f"Sent {self._send_count} screenshot(s). Next in {self._interval_text}.")
        except Exception as e:
            self._set_status(f"{'Test failed' if is_test else 'Send failed'}: {e}")
        finally:
            # Make sure the in-memory buffer is released.
            try:
                buf.close()
            except Exception:
                pass

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
    root = tk.Tk()
    App(root)
    root.mainloop()
