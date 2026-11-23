"""Optional role-tagged desktop video observations on pipewire-pulse."""
import json

from gi.repository import Gio, GLib

from .policy import Source


class VideoStreams:
    def __init__(self, runtime):
        self.runtime = runtime
        self.child = None
        self.keys = set()
        self.closed = False
        self.query = None
        self.dirty = False
        self.baseline = True
        self.bluetooth_active = set()
        self.fades = {}
        self.subscribe()

    def subscribe(self):
        if self.closed:
            return False
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        launcher.setenv("LC_ALL", "C", True)
        try:
            self.child = launcher.spawnv(["pactl", "subscribe"])
        except GLib.Error:
            return False
        self.baseline = True
        stream = Gio.DataInputStream.new(self.child.get_stdout_pipe())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.line)
        self.refresh()
        return False

    def line(self, stream, result):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if self.closed:
            return
        if line is None:
            self.child = None
            for key in self.keys:
                self.runtime.remove(key)
            self.keys.clear()
            self.bluetooth_active.clear()
            self.fades.clear()
            GLib.timeout_add_seconds(5, self.subscribe)
            return
        if "on sink-input " in line:
            self.refresh()
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.line)

    def refresh(self):
        if self.query is not None:
            self.dirty = True
            return
        try:
            self.query = Gio.Subprocess.new(["pactl", "-f", "json", "list", "sink-inputs"],
                                           Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
            self.query.communicate_utf8_async(None, None, self.received)
        except GLib.Error:
            self.query = None

    def received(self, proc, result):
        self.query = None
        try:
            ok, data, _ = proc.communicate_utf8_finish(result)
            if ok and proc.get_successful() and self.child and not self.closed:
                self.snapshot(json.loads(data))
        except (GLib.Error, ValueError, TypeError):
            pass
        if self.dirty and not self.closed and self.child:
            self.dirty = False
            self.refresh()

    def set_stream_volume(self, index, values):
        try:
            child = Gio.Subprocess.new(
                ["pactl", "set-sink-input-volume", str(index), *map(str, values)],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            child.wait_async(None, lambda proc, result: proc.wait_finish(result))
        except GLib.Error:
            pass

    def fade_step(self, key, step):
        fade = self.fades.get(key)
        if fade is None:
            return GLib.SOURCE_REMOVE
        index, original, _ = fade
        # PipeWire's Pulse volume scale is cubic. This makes the gain
        # increase smoothly and spends the first part close to silence.
        fraction = (step / 8) ** 2
        values = [round(value * fraction ** (1 / 3)) for value in original]
        self.fades[key] = (index, original, values)
        self.set_stream_volume(index, values)
        if step == 8:
            self.fades.pop(key, None)
        else:
            GLib.timeout_add(125 if step else 230, self.fade_step, key, step + 1)
        return GLib.SOURCE_REMOVE

    def fade_bluetooth(self, streams):
        if self.runtime.observe_only or not self.runtime.bluetooth_startup_fade:
            return
        relevant = {}
        active = set()
        for stream in streams:
            props = stream.get("properties", {})
            if (props.get("api.bluez5.profile") != "a2dp-source" or
                    not props.get("node.name", "").startswith("bluez_input.")):
                continue
            index = stream["index"]
            key = props.get("object.serial") or str(index)
            relevant[key] = stream
            if not stream["corked"]:
                active.add(key)
        for key in self.fades.keys() - relevant.keys():
            self.fades.pop(key)
        for key in self.fades.keys() - active:
            index, original, _ = self.fades.pop(key)
            if key in relevant:
                self.set_stream_volume(index, original)
        newly_active = active - self.bluetooth_active
        self.bluetooth_active = active
        if self.baseline:
            return
        for key in newly_active:
            stream = relevant[key]
            values = [channel["value"] for channel in stream.get("volume", {}).values()]
            if values and max(values) > 0 and not stream.get("mute"):
                self.fades[key] = (stream["index"], values, values)
                self.fade_step(key, 0)

    def snapshot(self, streams):
        self.fade_bluetooth(streams)
        tracked_pids = {pid for key, pid in self.runtime.players.pids.items() if key in self.runtime.policy.sources}
        seen = set()
        for stream in streams:
            props = stream.get("properties", {})
            if str(props.get("media.role", "")).lower() not in {"video", "movie"}:
                continue
            key = "video:" + str(props.get("object.serial") or stream["index"])
            if str(props.get("application.process.id", "")).isdigit() and int(props["application.process.id"]) in tracked_pids:
                # MPRIS now describes the same session. Retire the duplicate without granting replay.
                self.runtime.remove(key)
                self.keys.discard(key)
                continue
            seen.add(key)
            state = "paused" if stream.get("corked") else "playing"
            self.runtime.observe(Source(key, state, can_pause=False, can_play=False), self.baseline)
        for key in self.keys - seen:
            self.runtime.observe(Source(key, "stopped", can_pause=False, can_play=False))
            self.runtime.remove(key)
        self.keys = seen
        self.baseline = False

    def close(self):
        self.closed = True
        for index, original, _ in self.fades.values():
            self.set_stream_volume(index, original)
        self.fades.clear()
        if self.child:
            self.child.force_exit()
