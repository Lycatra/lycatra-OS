"""One GLib loop handles the session bus, player events, holds and deadlines."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import tomllib

from gi.repository import Gio, GLib

from .bus import NAME, PATH, INTERFACE
from .players import DBUS, DBUS_PATH, Players
from .policy import Policy


def diagnostic(key):
    if key is None:
        return None
    return "bluetooth" if key.startswith("bluetooth:") else key


class Runtime:
    def __init__(self, observe, trace):
        self.observe_only = observe
        self.bluetooth_startup_fade = False
        self.trace = trace
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.policy = Policy()
        self.players = None
        self.waiting = {}

    def log(self, *parts):
        if self.trace:
            print(*parts, file=sys.stderr, flush=True)

    def status(self):
        return {"foreground": diagnostic(self.policy.foreground), "holds": len(self.policy.holders),
                "sources": [{"source": diagnostic(p.key), "state": p.state} for p in self.policy.sources.values()]}

    def observe(self, source, initial=False, fresh=False):
        previous = self.policy.sources.get(source.key)
        if previous != source:
            self.log("observed", diagnostic(source.key), source.state)
        self.dispatch(self.policy.observe(source, initial, fresh))
        self.flush_acquires()

    def remove(self, key):
        self.policy.remove(key)
        self.flush_acquires()

    def accepted(self, cmd):
        self.dispatch(self.policy.accepted_reply(cmd))
        self.flush_acquires()

    def failed(self, cmd, reason):
        self.log(reason, diagnostic(cmd.source), cmd.action)
        self.policy.failed(cmd)
        self.flush_acquires()

    def dispatch(self, commands):
        for cmd in commands:
            self.log("would send" if self.observe_only else "send", cmd.action, diagnostic(cmd.source))
            if self.observe_only:
                self.policy.failed(cmd)
                continue
            if self.players is None:
                self.policy.failed(cmd)
                continue
            self.players.issue(cmd)
            def expire(command=cmd):
                current = self.policy.pending.get(command.source)
                if current and current[0] == command:
                    self.failed(command, "unconfirmed")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(2500, expire)

    def flush_acquires(self):
        if any(item[0].action == "pause" for item in self.policy.pending.values()):
            return
        for sender in list(self.waiting):
            self.reply_acquire(sender)

    def reply_acquire(self, sender):
        invocation = self.waiting.pop(sender, None)
        if invocation:
            invocation.return_value(None)
        return GLib.SOURCE_REMOVE

    def method(self, conn, sender, path, interface, method, parameters, invocation):
        if method == "Status":
            invocation.return_value(GLib.Variant("(s)", (json.dumps(self.status()),)))
        elif method == "Acquire":
            if sender in self.waiting:
                invocation.return_dbus_error(NAME+".AlreadyPending", "A hold from this connection is pending")
                return
            self.waiting[sender] = invocation
            self.dispatch(self.policy.acquire(sender))
            self.flush_acquires()
            if sender in self.waiting:
                GLib.timeout_add(450, self.reply_acquire, sender)
        elif method == "Release":
            self.reply_acquire(sender)
            self.dispatch(self.policy.release(sender))
            invocation.return_value(None)

    def owner_changed(self, conn, sender, path, interface, signal_name, params):
        name, old, new = params.unpack()
        if old and not new and name in self.policy.holders:
            self.waiting.pop(name, None)
            self.dispatch(self.policy.release(name))


def main():
    parser = argparse.ArgumentParser(description="Coordinate music, video and dictation playback.")
    parser.add_argument("--observe-only", action="store_true", help="Watch real events without issuing player commands or owning the control bus name")
    parser.add_argument("--trace", action="store_true", help="Log source transitions without media metadata")
    parser.add_argument("--status", action="store_true", help="Print the running service's state")
    opts = parser.parse_args()
    runtime = Runtime(opts.observe_only, opts.trace or opts.observe_only)
    if opts.status:
        result = runtime.bus.call_sync(NAME, PATH, NAME, "Status", None, None,
                                       Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        print(result.unpack()[0])
        return
    config_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config"))/"omarchy-media-focus/config.toml"
    with config_path.open("rb") if config_path.exists() else open(os.devnull, "rb") as config_file:
        config = tomllib.load(config_file)
    if not opts.observe_only:
        # Request the well-known name before subscribing or issuing any controls.
        reply = runtime.bus.call_sync(DBUS, DBUS_PATH, DBUS, "RequestName",
                                      GLib.Variant("(su)", (NAME, 4)), None,
                                      Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
        if reply != 1:
            sys.exit("Another media focus service owns the session")
        runtime.bus.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0], runtime.method, None, None)
        runtime.bus.signal_subscribe(DBUS, DBUS, "NameOwnerChanged", DBUS_PATH, None,
                                     Gio.DBusSignalFlags.NONE, runtime.owner_changed)
    runtime.bluetooth_startup_fade = config.get("bluetooth_startup_fade", False) is True
    runtime.players = Players(runtime, config.get("media_players", []), config.get("bluetooth_devices", []))
    if opts.observe_only:
        print(json.dumps(runtime.status()), flush=True)
    from .streams import VideoStreams
    streams = VideoStreams(runtime)
    loop = GLib.MainLoop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    try:
        loop.run()
    finally:
        streams.close()
