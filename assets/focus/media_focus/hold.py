import argparse
import os
import signal
import socket
import subprocess
import sys

from gi.repository import Gio, GLib

from .bus import NAME, PATH


def main():
    parser = argparse.ArgumentParser(description="Hold media focus while a command runs or until signalled.")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    command = options.command
    if command[:1] == ["--"]:
        command = command[1:]
    if options.wait == bool(command):
        parser.error("choose --wait or -- command [arguments]")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    def call(method):
        bus.call_sync(NAME, PATH, NAME, method, None, None, Gio.DBusCallFlags.NO_AUTO_START, 2500, None)
    call("Acquire")
    try:
        address = os.environ.get("NOTIFY_SOCKET")
        if address:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
                channel.sendto(b"READY=1", "\0" + address[1:] if address.startswith("@") else address)
        if command:
            return subprocess.run(command).returncode
        loop = GLib.MainLoop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: loop.quit() or GLib.SOURCE_REMOVE)
        watch = Gio.bus_watch_name_on_connection(bus, NAME, Gio.BusNameWatcherFlags.NONE,
                                                lambda *args: None, lambda *args: loop.quit())
        try:
            loop.run()
        finally:
            Gio.bus_unwatch_name(watch)
    finally:
        try:
            call("Release")
        except GLib.Error:
            pass
    return 0
