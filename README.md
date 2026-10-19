# Screen → Discord Webhook

A small local GUI app for your own PC. It captures a full-screen screenshot
and uploads it to a Discord webhook on an interval you choose
(seconds, minutes or hours). Capture all monitors or pick a single one.
Screenshots are held in memory and sent directly — nothing is written to disk.

**NOTE:** while running, this sends an image of your entire screen to
Discord on that interval. Only run it on a machine you intend to monitor.

**Windows only.** Mac and Linux are not supported yet.

## Setup

```
pip install -r requirements.txt
```

## Run

Double-click `Screenis.bat`, or:

```
pythonw screen_webhook.py
```

Paste your own Discord webhook URL, click **Test Webhook** to confirm it
works, then **Start**. The URL is saved locally to `config.json` (ignored by
git). Copy `config.example.json` to `config.json` if you'd rather set it by
hand.
