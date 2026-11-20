import { test } from 'node:test';
import assert from 'node:assert/strict';
import { encodeCapture, decodePlayback, PcmFrameQueue, FRAME_BYTES, KIND_PLAYBACK } from '../lib.mjs';

test('capture frame header', () => {
  const buf = encodeCapture('123456789012345678', '42', Buffer.from([1, 2, 3]));
  assert.equal(buf[0], 0x01);
  assert.equal(buf.readBigUInt64BE(1), 123456789012345678n);
  assert.equal(buf.readBigUInt64BE(9), 42n);
  assert.deepEqual([...buf.subarray(17)], [1, 2, 3]);
});

test('playback frame decode', () => {
  const header = Buffer.alloc(9);
  header.writeUInt8(KIND_PLAYBACK, 0);
  header.writeBigUInt64BE(10n, 1);
  const pb = decodePlayback(Buffer.concat([header, Buffer.from([9, 9])]));
  assert.equal(pb.guildId, '10');
  assert.deepEqual([...pb.pcm], [9, 9]);
  assert.equal(decodePlayback(Buffer.from([0x01, 0, 0])), null);
});

test('frame queue reassembles odd chunks into 20 ms frames', () => {
  const q = new PcmFrameQueue();
  q.push(Buffer.alloc(1000, 1));
  q.push(Buffer.alloc(FRAME_BYTES * 2 - 1000 + 10, 2));
  assert.equal(q.bufferedMs, 40);
  const a = q.nextFrame();
  const b = q.nextFrame();
  assert.equal(a.length, FRAME_BYTES);
  assert.equal(a[0], 1);
  assert.equal(a[999], 1);
  assert.equal(a[1000], 2);
  assert.equal(b.length, FRAME_BYTES);
  // trailing 10 bytes are padded with silence
  const c = q.nextFrame();
  assert.equal(c.length, FRAME_BYTES);
  assert.equal(c[9], 2);
  assert.equal(c[10], 0);
  assert.equal(q.nextFrame(), null);
});

test('clear drops buffered audio', () => {
  const q = new PcmFrameQueue();
  q.push(Buffer.alloc(FRAME_BYTES * 3));
  q.clear();
  assert.equal(q.length, 0);
});
