"""The serialized playback policy. Only the adapter executes returned commands."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    state: str
    track: str = ""
    can_pause: bool = True
    can_play: bool = True


@dataclass(frozen=True)
class Command:
    number: int
    source: str
    action: str


@dataclass(frozen=True)
class Resume:
    source: str
    track: str


class Policy:
    def __init__(self):
        self.sources = {}
        self.foreground = None
        self.predecessor = None
        self.held_source = None
        self.holders = set()
        self.pending = {}
        self.accepted = set()
        self.serial = 0

    def _issue(self, key, action, purpose):
        source = self.sources.get(key)
        if not source or key in self.pending:
            return []
        if action == "pause" and (not source.can_pause or source.state != "playing"):
            return []
        if action == "play" and (not source.can_play or source.state != "paused"):
            return []
        self.serial += 1
        cmd = Command(self.serial, key, action)
        self.pending[key] = (cmd, purpose, source.track)
        return [cmd]

    def failed(self, cmd):
        entry = self.pending.get(cmd.source)
        if entry and entry[0] == cmd:
            self.pending.pop(cmd.source)
        self.accepted.discard(cmd.number)

    def accepted_reply(self, cmd):
        entry = self.pending.get(cmd.source)
        if entry and entry[0] == cmd:
            self.accepted.add(cmd.number)
            return self._complete(self.sources[cmd.source], entry)
        return []

    def _complete(self, source, entry):
        cmd, purpose, track = entry
        expected = "paused" if cmd.action == "pause" else "playing"
        if cmd.number not in self.accepted or source.state != expected:
            return []
        key = source.key
        self.failed(cmd)
        if cmd.action == "play":
            if self.holders or (self.foreground and self.foreground != key):
                return self._issue(key, "pause", "hold" if key == self.foreground else "quiet")
            self.foreground = key
        elif track and source.track == track:
            candidate = Resume(key, track)
            if purpose == "hold" and key == self.foreground:
                if self.holders:
                    self.held_source = candidate
                else:
                    return self._restore(candidate)
            elif purpose == "handoff" and self.foreground != key:
                if self.foreground is None:
                    # The interrupting session explicitly stopped before our pause was confirmed.
                    return self._restore(candidate)
                self.predecessor = candidate
        return []

    def _forget(self, key):
        if self.held_source and self.held_source.source == key:
            self.held_source = None
        if self.predecessor and self.predecessor.source == key:
            self.predecessor = None

    def _restore(self, candidate):
        if candidate:
            source = self.sources.get(candidate.source)
            if source and source.track == candidate.track:
                return self._issue(source.key, "play", "restore")
        return []

    def remove(self, key):
        self.sources.pop(key, None)
        if key in self.pending:
            self.failed(self.pending[key][0])
        self._forget(key)
        if self.foreground == key:
            self.foreground = None
            self._cancel_predecessor()

    def _cancel_predecessor(self):
        self.predecessor = None
        for key, (cmd, purpose, track) in tuple(self.pending.items()):
            if purpose == "handoff":
                self.pending[key] = (cmd, "quiet", track)

    def acquire(self, holder):
        if holder in self.holders:
            return []
        self.holders.add(holder)
        if len(self.holders) > 1:
            return []
        self._cancel_predecessor()
        commands = []
        for source in self.sources.values():
            purpose = "hold" if source.key == self.foreground else "quiet"
            commands.extend(self._issue(source.key, "pause", purpose))
        return commands

    def release(self, holder):
        if holder not in self.holders:
            return []
        self.holders.remove(holder)
        if self.holders:
            return []
        candidate, self.held_source = self.held_source, None
        return self._restore(candidate)

    def observe(self, source, initial=False, fresh=False):
        key = source.key
        old = self.sources.get(key)
        self.sources[key] = source
        if old and old.track and source.track and old.track != source.track:
            self._forget(key)
        if old and old.state == source.state:
            return []

        entry = self.pending.get(key)
        if entry:
            cmd = entry[0]
            expected = "paused" if cmd.action == "pause" else "playing"
            if source.state == expected:
                return self._complete(source, entry)
            if source.state == "stopped":
                self.failed(cmd)

        if source.state == "playing":
            if self.holders:
                return self._issue(key, "pause", "hold" if key == self.foreground else "quiet")
            if initial or (old and old.state == "unknown" and not fresh):
                if self.foreground is None:
                    self.foreground = key
                return []
            winner_before = self.foreground
            self.foreground = key
            if winner_before == key:
                return []
            self.predecessor = None
            for pending_key, record in tuple(self.pending.items()):
                cmd, purpose, track = record
                if purpose in {"handoff", "hold"}:
                    self.pending[pending_key] = (cmd, "quiet", track)
            commands = []
            for other in self.sources.values():
                if other.key != key and other.state == "playing":
                    purpose = "handoff" if other.key == winner_before else "quiet"
                    commands.extend(self._issue(other.key, "pause", purpose))
            return commands

        if source.state in {"paused", "stopped"}:
            self._forget(key)
            if self.foreground == key:
                self.foreground = None
                candidate, self.predecessor = self.predecessor, None
                if not self.holders:
                    return self._restore(candidate)
        return []
