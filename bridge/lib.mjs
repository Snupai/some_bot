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

/**
 * Byte queue that hands out exact 20 ms PCM frames. It returns silence while
 * a short gap is bridged, and `null` once the queue has been idle long enough
 * that the player should stop. Model audio often arrives in bursts, so this
 * keeps one continuous Discord resource per spoken reply instead of
 * restarting the player for every chunk.
 */
export class PcmFrameQueue {
  constructor({ idleFrames = 15 } = {}) {
    this.chunks = [];
    this.length = 0;
    this.idleFrames = idleFrames;
    this.emptyReads = 0;
  }

  push(pcm) {
    if (!pcm || pcm.length === 0) return;
    this.chunks.push(pcm);
    this.length += pcm.length;
    this.emptyReads = 0;
  }

  clear() {
    this.chunks = [];
    this.length = 0;
  }

  get bufferedMs() {
    return Math.floor(this.length / FRAME_BYTES) * 20;
  }

  /** @returns {Buffer|null} a frame, a silence frame, or null when idle */
  nextFrame() {
    if (this.length >= FRAME_BYTES) {
      this.emptyReads = 0;
      return this.take(FRAME_BYTES);
    }
    if (this.length > 0) {
      // Pad the final partial frame with silence.
      const tail = this.take(this.length);
      const frame = Buffer.alloc(FRAME_BYTES);
      tail.copy(frame);
      this.emptyReads = 0;
      return frame;
    }
    this.emptyReads += 1;
    if (this.emptyReads > this.idleFrames) return null;
    return Buffer.alloc(FRAME_BYTES);
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
