import numpy as np
import pytest

from services.audio import (
    DiscordToLive,
    LiveToDiscord,
    mix,
    rms_dbfs,
    stereo48k_to_mono,
)


def sine(freq, rate, n, amp=10000.0):
    t = np.arange(n) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def stereo_bytes(mono):
    st = np.repeat(mono, 2)
    return np.clip(st, -32768, 32767).astype("<i2").tobytes()


def dominant_freq(x, rate):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / rate)[np.argmax(spec)]


def test_downsample_preserves_tone_and_length():
    mono48 = sine(1000, 48000, 48000)
    conv = DiscordToLive()
    out = b"".join(conv.convert_mono(stereo48k_to_mono(stereo_bytes(mono48[i:i + 960]))) for i in range(0, 48000, 960))
    y = np.frombuffer(out, dtype="<i2").astype(np.float32)
    assert len(y) == 24000
    assert abs(dominant_freq(y[1000:], 24000) - 1000) < 5
    # amplitude roughly preserved (passband gain ~1)
    assert 0.9 < np.max(np.abs(y[2000:])) / 10000 < 1.1


def test_downsample_removes_content_above_new_nyquist():
    mono48 = sine(15000, 48000, 48000)  # above 12 kHz -> must be filtered, not aliased to 9 kHz
    conv = DiscordToLive()
    y = np.frombuffer(conv.convert_mono(mono48), dtype="<i2").astype(np.float32)
    assert np.max(np.abs(y[200:])) < 10000 * 0.05


def test_streaming_matches_one_shot():
    mono48 = sine(440, 48000, 9600)
    a = DiscordToLive().convert_mono(mono48)
    conv = DiscordToLive()
    b = b"".join(conv.convert_mono(mono48[i:i + 960]) for i in range(0, 9600, 960))
    assert a == b


def test_upsample_handles_odd_chunks_and_doubles_rate():
    mono24 = sine(1000, 24000, 24000)
    pcm = mono24.astype("<i2").tobytes()
    conv = LiveToDiscord()
    # Feed in odd-sized chunks; a split sample must be carried, not dropped.
    out = b""
    i = 0
    for size in [1, 999, 3, 4097, 1]:
        out += conv.convert(pcm[i:i + size])
        i += size
    out += conv.convert(pcm[i:])
    st = np.frombuffer(out, dtype="<i2").reshape(-1, 2)
    assert st.shape[0] == 48000
    assert np.array_equal(st[:, 0], st[:, 1])
    assert abs(dominant_freq(st[2000:, 0].astype(np.float32), 48000) - 1000) < 5


def test_rms_dbfs():
    assert rms_dbfs(np.zeros(960, dtype=np.float32)) == pytest.approx(-120)
    full = np.full(960, 32767, dtype=np.float32)
    assert rms_dbfs(full) == pytest.approx(0, abs=0.01)
    assert rms_dbfs(full / 100) == pytest.approx(-40, abs=0.1)


def test_mix_soft_clips():
    a = np.full(960, 30000, dtype=np.float32)
    out = mix([a, a])
    assert np.max(out) <= 32767
    assert np.max(out) > 30000
