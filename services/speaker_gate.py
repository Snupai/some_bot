"""Speaker floor control and attribution.

GPT-Live hears one mixed audio stream, so it cannot know which Discord user
said what. The bot knows: each received frame carries the Discord user ID
from the voice bridge (SSRC -> user mapping from Discord's voice gateway).

* ``SpeakerGate`` decides which user frames are forwarded each 20 ms tick.
  In "floor" mode only one user is forwarded at a time, so each utterance has
  exactly one source.
* ``SpeakerTimeline`` records which users were audible at each point on the
  Live session clock (ms of audio sent since session start).
* ``attribute`` answers "who spoke right before this delegation?". Executing
  tools are only allowed if exactly one identifiable user spoke in that
  window; otherwise attribution fails and nothing is executed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .audio import FRAME_MS, mix


@dataclass
class GateResult:
    audio: np.ndarray | None  # mono 48 kHz float frame to forward, None = silence
    speakers: frozenset[int]  # users audible (voiced) in this tick
    floor_holder: int | None = None


class SpeakerGate:
    def __init__(self, mode: str = "floor", vad_threshold_dbfs: float = -45.0, floor_release_ms: int = 700) -> None:
        if mode not in {"floor", "mix"}:
            raise ValueError(mode)
        self.mode = mode
        self.threshold = vad_threshold_dbfs
        self.release_ticks = max(1, floor_release_ms // FRAME_MS)
        self.holder: int | None = None
        self._holder_silent_ticks = 0

    def reset(self) -> None:
        self.holder = None
        self._holder_silent_ticks = 0

    def drop_user(self, user_id: int) -> None:
        if self.holder == user_id:
            self.reset()

    def process(self, frames: dict[int, tuple[np.ndarray, float]]) -> GateResult:
        """``frames`` maps user_id -> (mono48 float frame, dBFS) for this tick."""
        voiced = {uid for uid, (_, db) in frames.items() if db >= self.threshold}

        if self.mode == "mix":
            # Only forward audio that is also recorded on the timeline (voiced frames).
            if not voiced:
                return GateResult(None, frozenset())
            return GateResult(mix([frames[u][0] for u in voiced]), frozenset(voiced))

        # floor mode
        if self.holder is not None:
            if self.holder in voiced:
                self._holder_silent_ticks = 0
            else:
                self._holder_silent_ticks += 1
                if self._holder_silent_ticks >= self.release_ticks:
                    self.holder = None
                    self._holder_silent_ticks = 0

        if self.holder is None and voiced:
            # Loudest voiced user takes the floor.
            self.holder = max(voiced, key=lambda u: frames[u][1])
            self._holder_silent_ticks = 0

        if self.holder is None:
            return GateResult(None, frozenset())

        entry = frames.get(self.holder)
        audio = entry[0] if entry is not None else None
        speakers = frozenset({self.holder}) if self.holder in voiced else frozenset()
        return GateResult(audio, speakers, self.holder)


@dataclass
class Attribution:
    user_id: int | None
    reason: str
    speakers: frozenset[int] = field(default_factory=frozenset)
    # Other users heard in the longer context window before the request. Their words are in the
    # model's context, so actions then need an explicit confirmation from the speaker.
    others_recent: frozenset[int] = field(default_factory=frozenset)
    offset_ms: int | None = None

    @property
    def ok(self) -> bool:
        return self.user_id is not None


class SpeakerTimeline:
    """Voiced ticks on the Live session clock. Keeps ``horizon_ms`` of history in memory."""

    def __init__(self, horizon_ms: int = 120_000) -> None:
        self.horizon_ms = horizon_ms
        self._ticks: deque[tuple[int, frozenset[int]]] = deque()

    def reset(self) -> None:
        self._ticks.clear()

    def record(self, t_ms: int, speakers: frozenset[int]) -> None:
        if speakers:
            self._ticks.append((t_ms, speakers))
        cutoff = t_ms - self.horizon_ms
        while self._ticks and self._ticks[0][0] < cutoff:
            self._ticks.popleft()

    def voiced_ms_by_user(self, start_ms: int, end_ms: int) -> dict[int, int]:
        out: dict[int, int] = {}
        for t, speakers in self._ticks:
            if start_ms <= t <= end_ms:
                for uid in speakers:
                    out[uid] = out.get(uid, 0) + FRAME_MS
        return out

    def last_speaker_before(self, t_ms: int) -> int | None:
        for t, speakers in reversed(self._ticks):
            if t <= t_ms and len(speakers) == 1:
                return next(iter(speakers))
        return None

    def attribute(self, offset_ms: int, window_ms: int, tolerance_ms: int, min_voiced_ms: int = 0,
                  context_ms: int = 0) -> Attribution:
        """Exactly one user must have produced (forwarded) speech in the window.

        ``min_voiced_ms`` defaults to 0: any forwarded speech from a second user, even a short
        "ja", makes the request ambiguous.
        """
        start = offset_ms - window_ms - tolerance_ms
        end = offset_ms + tolerance_ms
        by_user = self.voiced_ms_by_user(start, end)
        relevant = {uid: ms for uid, ms in by_user.items() if ms > 0 and ms >= min_voiced_ms}
        speakers = frozenset(relevant)
        if not relevant:
            return Attribution(None, "no_speech", frozenset(by_user), offset_ms=offset_ms)
        if len(relevant) > 1:
            return Attribution(None, "multiple_speakers", speakers, offset_ms=offset_ms)
        user = next(iter(relevant))
        others: frozenset[int] = frozenset()
        if context_ms > window_ms:
            ctx_users = self.voiced_ms_by_user(offset_ms - context_ms - tolerance_ms, end)
            others = frozenset(u for u in ctx_users if u != user)
        return Attribution(user, "single_speaker", speakers, others_recent=others, offset_ms=offset_ms)
