import numpy as np

from services.speaker_gate import SpeakerGate, SpeakerTimeline

LOUD = np.full(960, 8000, dtype=np.float32)
QUIET = np.zeros(960, dtype=np.float32)


def f(audio, db):
    return (audio, db)


def test_floor_first_speaker_keeps_floor():
    g = SpeakerGate("floor", -45, floor_release_ms=100)
    r = g.process({1: f(LOUD, -10)})
    assert r.floor_holder == 1 and r.speakers == {1}
    # user 2 starts talking (even louder) while 1 holds the floor -> not forwarded
    r = g.process({1: f(LOUD, -12), 2: f(LOUD * 2, -5)})
    assert r.floor_holder == 1 and r.speakers == {1}
    assert r.audio is LOUD


def test_floor_released_after_silence():
    g = SpeakerGate("floor", -45, floor_release_ms=60)  # 3 ticks
    g.process({1: f(LOUD, -10)})
    for _ in range(3):
        r = g.process({2: f(LOUD, -10)})
    # 1 was silent for 3 ticks -> released, 2 takes over on the same tick
    assert r.floor_holder == 2


def test_floor_ignores_quiet_users():
    g = SpeakerGate("floor", -45)
    r = g.process({1: f(QUIET, -80)})
    assert r.floor_holder is None and r.audio is None and not r.speakers


def test_mix_mode_reports_all_voiced():
    g = SpeakerGate("mix", -45)
    r = g.process({1: f(LOUD, -10), 2: f(LOUD, -12), 3: f(QUIET, -90)})
    assert r.speakers == {1, 2}
    assert r.audio is not None


def test_attribution_single_multiple_none():
    tl = SpeakerTimeline()
    for t in range(0, 2000, 20):
        tl.record(t, frozenset({1}))
    a = tl.attribute(2000, window_ms=3000, tolerance_ms=200)
    assert a.ok and a.user_id == 1

    for t in range(2000, 2600, 20):
        tl.record(t, frozenset({2}))
    a = tl.attribute(2600, window_ms=3000, tolerance_ms=200)
    assert not a.ok and a.reason == "multiple_speakers" and a.speakers == {1, 2}

    empty = SpeakerTimeline()
    a = empty.attribute(1000, 3000, 200)
    assert not a.ok and a.reason == "no_speech"


def test_attribution_ignores_old_speech_but_not_short_utterances():
    tl = SpeakerTimeline()
    for t in range(0, 1000, 20):  # user 9 spoke long ago (outside window)
        tl.record(t, frozenset({9}))
    for t in range(20000, 22000, 20):
        tl.record(t, frozenset({1}))
    a = tl.attribute(22000, window_ms=8000, tolerance_ms=500)
    assert a.ok and a.user_id == 1
    # Even a 20 ms forwarded "ja" from someone else makes the request ambiguous.
    tl.record(21000, frozenset({2}))
    a = tl.attribute(22000, window_ms=8000, tolerance_ms=500)
    assert not a.ok and a.reason == "multiple_speakers"


def test_attribution_reports_others_in_context_window():
    tl = SpeakerTimeline()
    for t in range(0, 1000, 20):
        tl.record(t, frozenset({7}))  # guest, 40 s before the request
    for t in range(40000, 42000, 20):
        tl.record(t, frozenset({1}))
    a = tl.attribute(42000, window_ms=8000, tolerance_ms=500, context_ms=60000)
    assert a.ok and a.user_id == 1 and a.others_recent == {7} and a.offset_ms == 42000


def test_mix_mode_does_not_forward_unvoiced_audio():
    g = SpeakerGate("mix", -45)
    r = g.process({1: f(QUIET, -90), 2: f(QUIET, -80)})
    assert r.audio is None and not r.speakers
    r = g.process({1: f(LOUD, -10), 2: f(LOUD * 0.001, -90)})
    assert r.speakers == {1}
    assert np.array_equal(r.audio, LOUD)  # quiet user 2 not mixed in


def test_timeline_horizon_prunes():
    tl = SpeakerTimeline(horizon_ms=1000)
    tl.record(0, frozenset({1}))
    tl.record(5000, frozenset({2}))
    assert tl.voiced_ms_by_user(0, 6000) == {2: 20}
