"""PCM audio helpers: format conversion, resampling, mixing and VAD.

Discord voice (via the bridge): signed 16-bit LE, 48 kHz, stereo, 20 ms frames.
GPT-Live (WebSocket):           signed 16-bit LE, 24 kHz, mono.

Resamplers keep filter state between chunks, so streaming audio has no clicks
at chunk boundaries. Nothing here writes audio to disk.
"""
from __future__ import annotations

import math

import numpy as np

DISCORD_RATE = 48000
DISCORD_CHANNELS = 2
DISCORD_FRAME_SAMPLES = 960  # per channel, 20 ms
DISCORD_FRAME_BYTES = DISCORD_FRAME_SAMPLES * DISCORD_CHANNELS * 2  # 3840
LIVE_RATE = 24000
LIVE_FRAME_SAMPLES = 480  # 20 ms mono at 24 kHz
LIVE_FRAME_BYTES = LIVE_FRAME_SAMPLES * 2
FRAME_MS = 20


def _lowpass_taps(numtaps: int, cutoff: float) -> np.ndarray:
    """Windowed-sinc low-pass. ``cutoff`` is a fraction of the sample rate (0..0.5)."""
    m = np.arange(numtaps) - (numtaps - 1) / 2.0
    h = 2 * cutoff * np.sinc(2 * cutoff * m)
    h *= np.blackman(numtaps)
    h /= h.sum()
    return h.astype(np.float32)


# Pass band to ~10.5 kHz at 48 kHz, well under the 12 kHz Nyquist of 24 kHz.
_HALFBAND = _lowpass_taps(47, 10500 / 48000)


def stereo48k_to_mono(pcm: bytes) -> np.ndarray:
    """Interleaved stereo int16 bytes -> mono float32 samples at 48 kHz."""
    s = np.frombuffer(pcm, dtype="<i2")
    if s.size % 2:
        s = s[:-1]
    st = s.reshape(-1, 2).astype(np.float32)
    return st.mean(axis=1)


def float_to_int16_bytes(x: np.ndarray) -> bytes:
    return np.clip(np.rint(x), -32768, 32767).astype("<i2").tobytes()


class Downsampler2x:
    """Stateful 48 kHz -> 24 kHz decimator (float32 in, float32 out)."""

    def __init__(self) -> None:
        self.taps = _HALFBAND
        self.hist = np.zeros(len(self.taps) - 1, dtype=np.float32)
        self.pos = 0  # global index of the first new sample

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.float32)
        buf = np.concatenate([self.hist, x.astype(np.float32)])
        y = np.convolve(buf, self.taps, mode="valid")  # len == len(x)
        start = (-self.pos) % 2
        out = y[start::2]
        self.pos += x.size
        self.hist = buf[-(len(self.taps) - 1):]
        return out


class Upsampler2x:
    """Stateful 24 kHz -> 48 kHz interpolator (float32 in, float32 out)."""

    def __init__(self) -> None:
        self.taps = (_HALFBAND * 2).astype(np.float32)
        self.hist = np.zeros(len(self.taps) - 1, dtype=np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x.astype(np.float32)
        z = np.zeros(x.size * 2, dtype=np.float32)
        z[::2] = x
        buf = np.concatenate([self.hist, z])
        y = np.convolve(buf, self.taps, mode="valid")
        self.hist = buf[-(len(self.taps) - 1):]
        return y


class DiscordToLive:
    """48 kHz stereo frames -> 24 kHz mono int16 bytes."""

    def __init__(self) -> None:
        self.down = Downsampler2x()

    def convert_mono(self, mono48: np.ndarray) -> bytes:
        return float_to_int16_bytes(self.down.process(mono48))


class LiveToDiscord:
    """24 kHz mono int16 byte stream (any chunking) -> 48 kHz stereo int16 bytes."""

    def __init__(self) -> None:
        self.up = Upsampler2x()
        self._carry = b""

    def convert(self, pcm24: bytes) -> bytes:
        data = self._carry + pcm24
        if len(data) % 2:
            # Live PCM chunks must be even-length, but tolerate a split sample anyway.
            self._carry, data = data[-1:], data[:-1]
        else:
            self._carry = b""
        if not data:
            return b""
        mono = np.frombuffer(data, dtype="<i2").astype(np.float32)
        up = self.up.process(mono)
        stereo = np.repeat(up, 2)
        return float_to_int16_bytes(stereo)

    def reset(self) -> None:
        self.up = Upsampler2x()
        self._carry = b""


def rms_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
    if rms <= 0:
        return -120.0
    return 20 * math.log10(rms / 32768.0)


def mix(frames: list[np.ndarray]) -> np.ndarray:
    """Mix equal-length float frames with soft limiting."""
    if not frames:
        return np.zeros(DISCORD_FRAME_SAMPLES, dtype=np.float32)
    if len(frames) == 1:
        return frames[0]
    total = np.sum(np.stack(frames), axis=0)
    # tanh soft clip keeps overlapping speakers intelligible instead of hard clipping.
    return (np.tanh(total / 32768.0) * 32767.0).astype(np.float32)


SILENCE_MONO48 = np.zeros(DISCORD_FRAME_SAMPLES, dtype=np.float32)
