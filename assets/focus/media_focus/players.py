"""Desktop MPRIS and Bluetooth AVRCP sessions, observed on their native buses."""
import hashlib
import json
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from gi.repository import Gio, GLib

from .policy import Source

PLAYER = "org.mpris.MediaPlayer2.Player"
MPRIS = "org.mpris.MediaPlayer2."
PLAYER_PATH = "/org/mpris/MediaPlayer2"
BLUEZ = "org.bluez"
MUSIC = {"spotify", "rhythmbox", "elisa", "strawberry", "clementine", "audacious", "lollypop",
         "cmus", "mpd", "amarok", "tauon", "ymuse", "tidal", "deezer", "deadbeef", "youtube-music"}
VIDEO = {"celluloid", "totem", "haruna", "smplayer", "mpv", "vlc"}
DOMAINS = {"youtube.com", "youtu.be", "open.spotify.com", "music.apple.com", "soundcloud.com",
           "netflix.com", "vimeo.com", "twitch.tv", "primevideo.com", "disneyplus.com", "nebula.tv",
           "bandcamp.com", "tidal.com", "deezer.com", "music.amazon.com"}
EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".opus", ".wav", ".ogg", ".alac", ".mp4", ".mkv",
              ".webm", ".mov", ".m4v", ".avi", ".m3u8"}
A2DP_SOURCE = "0000110a-0000-1000-8000-00805f9b34fb"
A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"


def token(value):
    if not value or not any(value):
        return ""
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()[:24]


def property_value(proxy, name, default=None):
    if proxy:
        value = proxy.get_cached_property(name)
        if value is not None:
            return value.unpack()
    return default


def recognized(name, metadata, allow):
    if name in allow:
        return True
    try:
        url = urlsplit(metadata.get("xesam:url", ""))
        host = (url.hostname or "").lower()
    except ValueError:
        return False
    app = set(name.removeprefix(MPRIS).lower().split("."))
    return (bool((MUSIC | VIDEO) & app) or PurePosixPath(url.path).suffix.lower() in EXTENSIONS
            or any(host == item or host.endswith("." + item) for item in DOMAINS)
            or bool(metadata.get("xesam:artist") and metadata.get("xesam:album")))


class Players:
    def __init__(self, runtime, allow, preferred):
        self.runtime = runtime
        self.allow = allow
        self.preferred = {address.upper() for address in preferred}
        self.session = runtime.bus
        self.entries = {}
        self.names = {}
        self.pids = {}
        self.phone_keys = set()
        self.phone_starts = {}
        self.manager = None
        self.session.signal_subscribe(DBUS, DBUS, "NameOwnerChanged", DBUS_PATH, None,
                                      Gio.DBusSignalFlags.NONE, self.name_changed)
        names = self.session.call_sync(DBUS, DBUS_PATH, DBUS, "ListNames", None, None,
                                       Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
        for name in names:
            self.add_mpris(name)
        try:
            self.manager = Gio.DBusObjectManagerClient.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START, BLUEZ, "/",
                None, None, None)
        except GLib.Error:
            return
        for signal in ("object-added", "object-removed", "interface-added", "interface-removed"):
            self.manager.connect(signal, self.bluetooth_objects)
        self.manager.connect("interface-proxy-properties-changed", self.bluetooth_properties)
        self.bluetooth_snapshot(initial=True)

    def name_changed(self, bus, sender, path, interface, signal, params):
        name, old, new = params.unpack()
        if not name.startswith(MPRIS):
            return
        if old and name in self.names:
            key = self.names.pop(name)
            if key not in self.names.values():
                self.entries.pop(key, None)
                self.pids.pop(key, None)
                self.runtime.remove(key)
        if new:
            self.add_mpris(name, new, initial=False)

    def add_mpris(self, name, owner=None, initial=True):
        if not name.startswith(MPRIS) or name.startswith(MPRIS + "kdeconnect"):
            return
        try:
            if not owner:
                owner = self.session.call_sync(DBUS, DBUS_PATH, DBUS, "GetNameOwner",
                                               GLib.Variant("(s)", (name,)), None,
                                               Gio.DBusCallFlags.NONE, 700, None).unpack()[0]
            key = "mpris:" + owner
            self.names[name] = key
            if key in self.entries:
                return
            proxy = Gio.DBusProxy.new_sync(self.session, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                                           None, owner, PLAYER_PATH, PLAYER, None)
            self.entries[key] = proxy
            self.pids[key] = self.session.call_sync(DBUS, DBUS_PATH, DBUS, "GetConnectionUnixProcessID",
                                                     GLib.Variant("(s)", (owner,)), None,
                                                     Gio.DBusCallFlags.NONE, 700, None).unpack()[0]
            proxy.connect("g-properties-changed", self.mpris_changed, name, key)
            self.mpris_update(proxy, name, key, initial=initial)
        except GLib.Error:
            return

    def mpris_changed(self, proxy, changed, invalid, name, key):
        if self.entries.get(key) is proxy:
            self.mpris_update(proxy, name, key)

    def mpris_update(self, proxy, name, key, initial=False):
        meta = property_value(proxy, "Metadata", {})
        if not recognized(name, meta, self.allow):
            self.runtime.remove(key)
            return
        state = property_value(proxy, "PlaybackStatus", "unknown").lower()
        track = token([meta.get("mpris:trackid") or meta.get("xesam:url") or meta.get("xesam:title")])
        control = property_value(proxy, "CanControl", False)
        self.runtime.observe(Source(key, state, track, control and property_value(proxy, "CanPause", False),
                                    control and property_value(proxy, "CanPlay", False)), initial)

    def bluetooth_objects(self, *unused):
        self.bluetooth_snapshot()

    def bluetooth_properties(self, manager, obj, proxy, changed, invalid):
        if proxy.get_interface_name() == BLUEZ + ".MediaPlayer1" and changed.unpack().get("Status") == "playing":
            self.phone_starts[obj.get_object_path()] = time.monotonic()
        if proxy.get_interface_name() in (BLUEZ + ".MediaTransport1", BLUEZ + ".MediaPlayer1",
                                         BLUEZ + ".MediaControl1", BLUEZ + ".Device1"):
            self.bluetooth_snapshot()

    def bluetooth_snapshot(self, initial=False):
        objects = self.manager.get_objects()
        phones = {}
        players = {}
        routes = set()
        control = {}
        for obj in objects:
            path = obj.get_object_path()
            device = obj.get_interface(BLUEZ + ".Device1")
            if device:
                address = property_value(device, "Address", "").upper()
                if (property_value(device, "Connected") and property_value(device, "Paired")
                        and A2DP_SOURCE in property_value(device, "UUIDs", [])
                        and (not self.preferred or address in self.preferred)):
                    phones[path] = device
            selected = obj.get_interface(BLUEZ + ".MediaControl1")
            if selected:
                control[path] = property_value(selected, "Player")
            player = obj.get_interface(BLUEZ + ".MediaPlayer1")
            if player:
                players[path] = player
            route = obj.get_interface(BLUEZ + ".MediaTransport1")
            if (route and property_value(route, "UUID") in (A2DP_SOURCE, A2DP_SINK)
                    and property_value(route, "State") in ("pending", "active")):
                routes.add(property_value(route, "Device"))
        seen = set()
        for path in phones:
            options = [p for p in players if property_value(players[p], "Device") == path or p.startswith(path + "/")]
            target = control.get(path)
            if target not in options:
                target = options[0] if len(options) == 1 else None
            if not target:
                continue
            player = players[target]
            key = "bluetooth:" + target
            seen.add(key)
            prior = self.runtime.policy.sources.get(key)
            self.entries[key] = player
            state = property_value(player, "Status", "unknown")
            # AVRCP identifies intent. A2DP corroborates routing. Losing the
            # transport during playback cannot authorize another player's Play.
            if state not in {"playing", "paused", "stopped"}:
                state = "unknown"
            if state == "playing" and path not in routes:
                state = "unknown"
            meta = property_value(player, "Track", {})
            track = token([meta.get(k) for k in ("Title", "Artist", "Album", "Duration")])
            fresh = time.monotonic() - self.phone_starts.get(target, -100) < 4
            self.runtime.observe(Source(key, state, track), initial or (prior is None and not fresh), fresh)
            if state == "playing":
                self.phone_starts.pop(target, None)
        for key in self.phone_keys - seen:
            self.entries.pop(key, None)
            self.phone_starts.pop(key.removeprefix("bluetooth:"), None)
            self.runtime.remove(key)
        self.phone_keys = seen

    def issue(self, cmd):
        proxy = self.entries.get(cmd.source)
        if not proxy:
            self.runtime.failed(cmd, "player vanished")
            return

        def replied(proxy, result):
            try:
                proxy.call_finish(result)
                self.runtime.accepted(cmd)
            except GLib.Error:
                self.runtime.failed(cmd, "player rejected command")

        proxy.call(cmd.action.capitalize(), None, Gio.DBusCallFlags.NONE, 1500, None, replied)
