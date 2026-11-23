import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/focus"))

from media_focus.policy import Policy, Source


class Playback(unittest.TestCase):
    def setUp(self):
        self.policy = Policy()
        self.pc = Source("desktop", "playing", "x")
        self.phone = Source("phone", "playing", "y")

    def event(self, player, state=None, **kwargs):
        return self.policy.observe(
            replace(player, state=state) if state else player, **kwargs
        )

    def command(self, cmds, source, action):
        self.assertEqual([(c.source, c.action) for c in cmds], [(source.key, action)])
        return cmds[0]

    def handoff(self, first, second):
        self.assertEqual(self.event(first, initial=True), [])
        cmd = self.command(self.event(second), first, "pause")
        self.assertEqual(self.policy.accepted_reply(cmd), [])
        self.assertEqual(self.event(first, "paused"), [])

    def test_latest_in_both_directions_and_manual_pause_resumes_once(self):
        for first, second in [(self.pc, self.phone), (self.phone, self.pc)]:
            self.policy = Policy()
            self.handoff(first, second)
            cmd = self.command(self.event(second, "paused"), first, "play")
            self.assertEqual(self.policy.accepted_reply(cmd), [])
            self.assertEqual(self.event(first, "playing"), [])
            self.assertEqual(self.event(second, "paused"), [])

    def test_user_restarts_just_paused_player(self):
        self.handoff(self.pc, self.phone)
        self.command(self.event(self.pc, "playing"), self.phone, "pause")

    def test_failed_pause_then_manual_pause_does_not_autoplay(self):
        self.event(self.phone, initial=True)
        cmd = self.command(self.policy.acquire("recorder"), self.phone, "pause")
        self.event(self.phone, "paused")
        self.policy.failed(cmd)
        self.assertEqual(self.policy.release("recorder"), [])

    def test_quick_release_before_acknowledgement(self):
        self.event(self.phone, initial=True)
        cmd = self.command(self.policy.acquire("recorder"), self.phone, "pause")
        self.assertEqual(self.policy.release("recorder"), [])
        self.assertEqual(self.event(self.phone, "paused"), [])
        self.command(self.policy.accepted_reply(cmd), self.phone, "play")

    def test_nested_holders_wait_for_last_release(self):
        self.event(self.pc, initial=True)
        cmd = self.command(self.policy.acquire("first"), self.pc, "pause")
        self.assertEqual(self.policy.acquire("second"), [])
        self.policy.accepted_reply(cmd)
        self.event(self.pc, "paused")
        self.assertEqual(self.policy.release("first"), [])
        self.command(self.policy.release("second"), self.pc, "play")

    def test_dictation_retires_prior_resume(self):
        self.handoff(self.pc, self.phone)
        hold = self.command(self.policy.acquire("recorder"), self.phone, "pause")
        self.event(self.phone, "paused")
        self.policy.accepted_reply(hold)
        resume = self.command(self.policy.release("recorder"), self.phone, "play")
        self.policy.accepted_reply(resume)
        self.event(self.phone)
        self.assertEqual(self.event(self.phone, "paused"), [])

    def test_overlapping_handoff_command_cannot_restore_previous_after_dictation(self):
        self.event(self.pc, initial=True)
        handoff = self.command(self.event(self.phone), self.pc, "pause")
        hold = self.command(self.policy.acquire("recorder"), self.phone, "pause")
        self.event(self.pc, "paused")
        self.policy.accepted_reply(handoff)
        self.assertIsNone(self.policy.predecessor)
        self.event(self.phone, "paused")
        self.policy.accepted_reply(hold)
        self.command(self.policy.release("recorder"), self.phone, "play")

    def test_no_resume_on_bluetooth_telemetry_loss(self):
        self.handoff(self.pc, self.phone)
        self.assertEqual(self.event(self.phone, "unknown"), [])
        self.assertEqual(self.event(self.phone, "unknown"), [])
        self.policy.remove(self.phone.key)
        self.assertIsNone(self.policy.predecessor)

    def test_stale_or_changed_track_cannot_resume(self):
        self.handoff(self.pc, self.phone)
        self.policy.observe(replace(self.pc, state="paused", track="different"))
        self.assertEqual(self.event(self.phone, "stopped"), [])

    def test_phone_route_after_avrcp_intent_selects_new_source(self):
        self.event(self.pc, initial=True)
        self.event(self.phone, "paused", initial=True)
        self.assertEqual(self.event(self.phone, "unknown"), [])
        self.command(self.event(self.phone, fresh=True), self.pc, "pause")

    def test_transport_only_change_cannot_promote_phone(self):
        self.event(self.pc, initial=True)
        self.event(self.phone, "unknown", initial=True)
        self.assertEqual(self.event(self.phone), [])
        self.assertEqual(self.policy.foreground, self.pc.key)

    def test_initial_snapshot_is_passive(self):
        self.event(self.pc, initial=True)
        self.assertEqual(self.event(self.phone, initial=True), [])

    def test_unknown_track_never_auto_resumes(self):
        source = replace(self.phone, track="")
        self.handoff(source, self.pc)
        self.assertEqual(self.event(self.pc, "stopped"), [])

    def test_passive_video_can_interrupt_but_cannot_be_commanded(self):
        video = Source("video", "playing", "", False, False)
        self.handoff(self.pc, video)
        self.command(self.event(video, "stopped"), self.pc, "play")

    def test_release_when_player_missing_stays_quiet(self):
        self.event(self.phone, initial=True)
        hold = self.command(self.policy.acquire("recorder"), self.phone, "pause")
        self.event(self.phone, "paused")
        self.policy.accepted_reply(hold)
        self.policy.remove(self.phone.key)
        self.assertEqual(self.policy.release("recorder"), [])

    def test_short_interruption_ends_before_prior_pause_confirmed(self):
        self.event(self.pc, initial=True)
        cmd = self.command(self.event(self.phone), self.pc, "pause")
        self.event(self.phone, "stopped")
        self.event(self.pc, "paused")
        self.command(self.policy.accepted_reply(cmd), self.pc, "play")

    def test_disconnection_during_unconfirmed_handoff_discards_resume(self):
        self.event(self.pc, initial=True)
        cmd = self.command(self.event(self.phone), self.pc, "pause")
        self.policy.remove(self.phone.key)
        self.event(self.pc, "paused")
        self.assertEqual(self.policy.accepted_reply(cmd), [])
        self.assertIsNone(self.policy.predecessor)
