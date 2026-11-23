"""Read call state from the paired Android device without returning call content."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

PIXEL = os.environ.get("LYCATRA_PHONE_ADB", "")
SIGNAL = "org.thoughtcrime.securesms"
STATUS = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid()))) / "omarchy-phone-call.json"


def adb(*args):
    return subprocess.check_output(["adb", "-s", PIXEL, "shell", *args],
                                   text=True, timeout=4, stderr=subprocess.DEVNULL)


def app_name(package):
    return {SIGNAL: "Signal", "com.whatsapp": "WhatsApp", "org.telegram.messenger": "Telegram",
            "com.discord": "Discord", "com.google.android.apps.tachyon": "Google Meet"}.get(package, "Phone app")


def detect():
    state, audio = adb("dumpsys", "telephony.registry", ";", "dumpsys", "audio").split(
        "## Audio mode:", 1)
    def values(field):
        return set(map(int, re.findall(rf"^\s*{field}=(\d+)\s*$", state, re.MULTILINE)))
    foreground = values("mForegroundCallState")
    if 4 in foreground or 1 in values("mRingingCallState"):
        return "Ringing", "Pixel"
    if 1 in foreground:
        return "Connected", "Pixel"
    if 3 in foreground:
        return "Dialing", "Pixel"
    if 2 in foreground:
        return "On hold", "Pixel"
    if values("mCallState") - {0}:
        return "Call in progress", "Pixel"

    owner = audio.split("- Mode owner:", 1)[-1].split("- Mode owner stack:", 1)[0]
    package = re.search(r"^\s*- Package: ([\w.]+)\s*$", owner, re.M)
    if ("MODE_IN_COMMUNICATION" in owner and package and
            re.search(r"^\s*- Active: true\s*$", owner, re.M)):
        return "Connected", app_name(package[1])

    notifications = adb("dumpsys", "notification")
    active = notifications.split("Notification List:", 1)[-1].split("Enqueued Notification List:", 1)[0]
    for section in re.split(r"(?m)^\s*NotificationRecord\(", active)[1:]:
        header = section.splitlines()[0]
        package = re.search(r"\bpkg=([\w.]+) ", header)
        if not package or "category=call" not in header:
            continue
        channel = re.search(r"\bchannel=([^ )]+)", header)
        if channel and re.search(r"missed|history", channel[1], re.I):
            continue
        flags = re.search(r"\bflags=(0x[0-9a-fA-F]+)", header)
        incoming = bool(re.search(r"fullScreenIntent=PendingIntent|android.callType=1\b", section))
        if channel and re.search(r"incoming|ringing", channel[1], re.I):
            incoming = True
        if incoming or "ONGOING_EVENT" in header or (flags and int(flags[1], 16) & 2):
            return ("Ringing" if incoming else "Call in progress"), app_name(package[1])

    return "", ""


def snapshot():
    try:
        return json.loads(STATUS.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def announce():
    command = ["/usr/share/omarchy/bin/omarchy-shell", "seb-krz.omarchy-connect"]
    try:
        subprocess.check_output([*command, "messagesStatus"], timeout=3, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    # An IPC timeout after dispatch can still mean the panel opened.
    # Never send a second open for the same call.
    try:
        subprocess.run([*command, "openCurrentCall"], timeout=4, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def unload_finished_call_filters():
    # The call panel can be destroyed before its worker runs finally.
    # These modules are safe to reap only after their owning process exits.
    listing = subprocess.check_output(["pactl", "list", "short", "modules"],
                                      text=True, timeout=3, stderr=subprocess.DEVNULL)
    for line in listing.splitlines():
        fields = line.split("\t", 3)
        if len(fields) < 3 or not fields[0].isdigit():
            continue
        module, args = fields[1:3]
        if module == "module-echo-cancel":
            owner = re.search(r"(?:^| )source_name=omarchy_pixel_call_mic_(\d+)(?: |$)", args)
        elif module == "module-null-sink":
            owner = re.search(r"(?:^| )sink_name=omarchy_pixel_call_share_(\d+)(?: |$)", args)
        else:
            continue
        if owner and not Path("/proc", owner[1]).exists():
            subprocess.run(["pactl", "unload-module", fields[0]], timeout=3, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def watch():
    lease = None
    status = {}
    connected_at = 0
    call_id = 0
    announced = False
    idle_readings = 0
    helper = Path.home() / ".local/bin/media-focus-hold"
    try:
        while True:
            try:
                state, app = detect()
            except (OSError, ValueError, subprocess.SubprocessError):
                # An unreachable phone is not evidence the call ended.
                time.sleep(2)
                continue
            if state:
                idle_readings = 0
                if not call_id:
                    call_id = time.time_ns()
                    announced = False
                if lease is None or lease.poll() is not None:
                    lease = subprocess.Popen([helper, "--wait"], stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                if state == "Connected" and not connected_at:
                    connected_at = int(time.time() * 1000)
            else:
                idle_readings += 1
                if call_id and idle_readings < 2:
                    time.sleep(1.5)
                    continue
                if idle_readings == 2:
                    try:
                        unload_finished_call_filters()
                    except (OSError, subprocess.SubprocessError):
                        pass
                call_id = 0
                connected_at = 0
                if lease is not None:
                    lease.terminate()
                    lease.wait()
                    lease = None
            current = {"state": state, "app": app, "connectedAt": connected_at}
            if current != status:
                temp = STATUS.with_suffix(".tmp")
                temp.write_text(json.dumps(current))
                temp.replace(STATUS)
                status = current
            if call_id and not announced:
                announced = announce()
            time.sleep(1.5)
    finally:
        if lease is not None:
            lease.terminate()
            lease.wait()


if __name__ == "__main__":
    if sys.argv[1:] == ["--watch"]:
        watch()
    elif sys.argv[1:] == ["--probe"]:
        print(json.dumps(detect()))
    else:
        print(json.dumps(snapshot()))
