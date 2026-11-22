// Wire format shared with services/discord_audio.py. Keep both sides in sync.
//
// Text frames carry JSON control messages ({op: ...}).
// Binary frames carry PCM audio, signed 16-bit little-endian, 48 kHz, stereo:
//   0x01 | guildId (u64 BE) | userId (u64 BE) | pcm   (bridge -> bot: received speech)
//   0x02 | guildId (u64 BE) | pcm                     (bot -> bridge: audio to play)

export const KIND_CAPTURE = 0x01;
export const KIND_PLAYBACK = 0x02;

export const SAMPLE_RATE = 48000;
export const CHANNELS = 2;
export const FRAME_SAMPLES = 960; // 20 ms per channel
export const FRAME_BYTES = FRAME_SAMPLES * CHANNELS * 2; // 3840

export function encodeCapture(guildId, userId, pcm) {
  const header = Buffer.alloc(17);
  header.writeUInt8(KIND_CAPTURE, 0);
  header.writeBigUInt64BE(BigInt(guildId), 1);
  header.writeBigUInt64BE(BigInt(userId), 9);
  return Buffer.concat([header, pcm]);
}

export function decodePlayback(buf) {
  if (buf.length < 9 || buf.readUInt8(0) !== KIND_PLAYBACK) return null;
  return {
    guildId: buf.readBigUInt64BE(1).toString(),
    pcm: buf.subarray(9),
  };
}

/** Byte queue that hands out exact 20 ms PCM frames (the last one padded with silence). */
export class PcmFrameQueue {
  constructor() {
    this.chunks = [];
    this.length = 0;
  }

  push(pcm) {
    if (!pcm || pcm.length === 0) return;
    this.chunks.push(pcm);
    this.length += pcm.length;
  }

  clear() {
    this.chunks = [];
    this.length = 0;
  }

  get bufferedMs() {
    return Math.floor(this.length / FRAME_BYTES) * 20;
  }

  /** @returns {Buffer|null} the next frame, or null when empty */
  nextFrame() {
    if (this.length >= FRAME_BYTES) return this.take(FRAME_BYTES);
    if (this.length === 0) return null;
    // Pad the final partial frame with silence.
    const frame = Buffer.alloc(FRAME_BYTES);
    this.take(this.length).copy(frame);
    return frame;
  }

  take(n) {
    const out = Buffer.alloc(n);
    let off = 0;
    while (off < n) {
      const head = this.chunks[0];
      const need = n - off;
      if (head.length <= need) {
        head.copy(out, off);
        off += head.length;
        this.chunks.shift();
      } else {
        head.copy(out, off, 0, need);
        this.chunks[0] = head.subarray(need);
        off += need;
      }
    }
    this.length -= n;
    return out;
  }
}
