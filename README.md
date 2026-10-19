# Screen → Discord Webhook

A small local GUI app for your own PC. It captures a full-screen screenshot
and uploads it to a Discord webhook on an interval you choose
(seconds, minutes or hours). Capture all monitors or pick a single one.
Screenshots are held in memory and sent directly — nothing is written to disk.

**NOTE:** while running, this sends an image of your entire screen to
Discord on that interval. Only run it on a machine you intend to monitor.

**Windows only.** Mac and Linux are not supported yet.

## Easy install (no Python needed)

1. Click [Screenis.exe](Screenis.exe) in this repo, then the download button.
2. Put it in its own folder (it saves `config.json` next to itself).
3. Double-click it. If Windows SmartScreen warns about an unknown app,
   click **More info → Run anyway**.

## Run from source

```
pip install -r requirements.txt
```

Double-click `Screenis.bat`, or:

```
pythonw screen_webhook.py
```
## Discord Setup
In your own discord server or any server you can edit, create a new or select a text channel you want
then open its settings, you can find the integrations tab then click create webhook.

<img width="1108" height="612" alt="image" src="https://github.com/user-attachments/assets/e8d68f62-112b-4b7d-96b3-9dcf63b2a572" />

Customize the Webhook bot however you'd like then click "Copy Webhook URL"

<img width="1096" height="773" alt="image" src="https://github.com/user-attachments/assets/2effa76c-75b6-4b55-9445-7cac1d35ea52" />
