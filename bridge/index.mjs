#!/usr/bin/env node
// Nyanjou voice bridge.
//
// Pycord cannot receive voice under Discord's DAVE end-to-end encryption
// (https://github.com/Pycord-Development/pycord/issues/3139). This sidecar
// runs the voice connection (UDP, transport encryption, DAVE, Opus) with
// @discordjs/voice. The Python bot keeps the gateway connection: it sends
// voice state updates for us and forwards VOICE_STATE_UPDATE and
// VOICE_SERVER_UPDATE payloads here, the same split Lavalink uses.
//
// It listens on loopback only and requires a shared token.

import { WebSocketServer } from 'ws';
import { timingSafeEqual } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import {
  joinVoiceChannel,
  getVoiceConnection,
  VoiceConnectionStatus,
  EndBehaviorType,
  entersState,
} from '@discordjs/voice';
import opus from '@discordjs/opus';
import {
  encodeCapture,
  decodePlayback,
  PcmFrameQueue,
  SAMPLE_RATE,
  CHANNELS,
  FRAME_BYTES,
} from './lib.mjs';

const HOST = process.env.NYANJOU_BRIDGE_HOST || '127.0.0.1';
const PORT = Number(process.env.NYANJOU_BRIDGE_PORT || 8765);
const TOKEN = process.env.NYANJOU_BRIDGE_TOKEN || '';
const DEBUG = /^(1|true|yes)$/i.test(process.env.NYANJOU_BRIDGE_DEBUG || '');
const OPUS_SILENCE = Buffer.from([0xf8, 0xff, 0xfe]);
const LOOPBACK = new Set(['127.0.0.1', '::1', 'localhost']);

if (!TOKEN || TOKEN.length < 16) {
  console.error('[bridge] NYANJOU_BRIDGE_TOKEN must be set (>= 16 chars).');
  process.exit(2);
}
if (!LOOPBACK.has(HOST) && !/^(1|true)$/i.test(process.env.NYANJOU_BRIDGE_ALLOW_REMOTE || '')) {
  console.error('[bridge] Refusing to bind a non-loopback host without NYANJOU_BRIDGE_ALLOW_REMOTE=true.');
  process.exit(2);
}

const log = (...a) => console.log('[bridge]', ...a);
const debug = (...a) => DEBUG && console.log('[bridge:debug]', ...a);

/** @type {import('ws').WebSocket | null} */
let bot = null;
/** guildId -> GuildVoice */
const guilds = new Map();

function send(msg) {
  if (bot && bot.readyState === 1) bot.send(JSON.stringify(msg));
}

function sendBinary(buf) {
  // Drop audio instead of queueing it when the socket is backed up (latency > completeness).
  if (bot && bot.readyState === 1 && bot.bufferedAmount < 1 << 20) bot.send(buf);
}

class GuildVoice {
  constructor(guildId, channelId, selfUserId) {
    this.guildId = guildId;
    this.channelId = channelId;
    this.selfUserId = selfUserId;
    this.adapterMethods = null;
    this.encoder = new opus.OpusEncoder(SAMPLE_RATE, CHANNELS);
    this.decoders = new Map(); // userId -> {decoder, stream}
    this.queue = new PcmFrameQueue({ idleFrames: 10 });
    this.timer = null;
    this.speaking = false;
    this.silenceFramesLeft = 0;
    this.nextTick = 0;
    this.connection = null;
  }

  connect() {
    const self = this;
    this.connection = joinVoiceChannel({
      guildId: this.guildId,
      channelId: this.channelId,
      selfDeaf: false,
      selfMute: false,
      daveEncryption: true,
      debug: DEBUG,
      adapterCreator(methods) {
        self.adapterMethods = methods;
        return {
          sendPayload(payload) {
            send({ op: 'gateway', guildId: self.guildId, payload });
            return true;
          },
          destroy() {
            self.adapterMethods = null;
          },
        };
      },
    });

    const c = this.connection;
    c.on('stateChange', (oldState, newState) => {
      debug(`guild ${this.guildId}: ${oldState.status} -> ${newState.status}`);
      send({ op: 'connection_state', guildId: this.guildId, state: newState.status });
      if (newState.status === VoiceConnectionStatus.Disconnected) this.handleDisconnect();
    });
    c.on('error', (err) => {
      log(`guild ${this.guildId} connection error:`, err?.message || err);
      send({ op: 'error', guildId: this.guildId, message: String(err?.message || err) });
    });
    if (DEBUG) c.on('debug', (m) => debug(`guild ${this.guildId}:`, m));

    c.receiver.speaking.on('start', (userId) => this.onSpeakingStart(userId));
    c.receiver.speaking.on('end', (userId) => send({ op: 'speaking_end', guildId: this.guildId, userId }));
  }

  async handleDisconnect() {
    // Recover from a channel move or brief network loss; otherwise destroy.
    try {
      await Promise.race([
        entersState(this.connection, VoiceConnectionStatus.Signalling, 5000),
        entersState(this.connection, VoiceConnectionStatus.Connecting, 5000),
      ]);
    } catch {
      log(`guild ${this.guildId}: voice connection lost, destroying`);
      this.destroy('disconnected');
    }
  }

  onSpeakingStart(userId) {
    send({ op: 'speaking_start', guildId: this.guildId, userId });
    if (userId === this.selfUserId || this.decoders.has(userId)) return;
    const stream = this.connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.Manual },
    });
    const decoder = new opus.OpusEncoder(SAMPLE_RATE, CHANNELS);
    this.decoders.set(userId, { decoder, stream });
    stream.on('data', (packet) => {
      let pcm;
      try {
        pcm = decoder.decode(packet);
      } catch (err) {
        debug(`decode failed for ${userId}:`, err?.message);
        return;
      }
      if (pcm.length !== FRAME_BYTES) {
        // Opus may carry 40/60 ms frames. Split them into 20 ms frames for the bot.
        for (let off = 0; off + FRAME_BYTES <= pcm.length; off += FRAME_BYTES) {
          sendBinary(encodeCapture(this.guildId, userId, pcm.subarray(off, off + FRAME_BYTES)));
        }
        return;
      }
      sendBinary(encodeCapture(this.guildId, userId, pcm));
    });
    const cleanup = () => this.decoders.delete(userId);
    stream.once('end', cleanup);
    stream.once('close', cleanup);
    stream.once('error', cleanup);
  }

  onVoiceState(d) {
    // Only our own voice state arrives here. The bot sends `forget_user` for others who leave.
    this.adapterMethods?.onVoiceStateUpdate(d);
  }

  onVoiceServer(d) {
    this.adapterMethods?.onVoiceServerUpdate(d);
  }

  forgetUser(userId) {
    const entry = this.decoders.get(userId);
    if (entry) {
      entry.stream.destroy();
      this.decoders.delete(userId);
    }
  }

  enqueue(pcm) {
    this.queue.push(pcm);
    if (!this.timer) {
      this.nextTick = performance.now();
      this.tick();
    }
  }

  flush() {
    this.queue.clear();
    // Send a few Opus silence frames so receivers don't interpolate the cut.
    this.silenceFramesLeft = this.speaking ? 5 : 0;
  }

  tick() {
    this.timer = null;
    const c = this.connection;
    if (!c || c.state.status !== VoiceConnectionStatus.Ready) {
      // Not ready yet: keep the audio and retry shortly.
      if (this.queue.length > 0) this.timer = setTimeout(() => this.tick(), 50);
      return;
    }

    const frame = this.queue.length > 0 ? this.queue.nextFrame() : null;

    if (frame) {
      if (!this.speaking) {
        c.setSpeaking(true);
        this.speaking = true;
      }
      c.playOpusPacket(this.encoder.encode(frame));
      this.silenceFramesLeft = 5;
    } else if (this.speaking && this.silenceFramesLeft > 0) {
      c.playOpusPacket(OPUS_SILENCE);
      this.silenceFramesLeft -= 1;
    } else if (this.speaking) {
      c.setSpeaking(false);
      this.speaking = false;
      send({ op: 'playback_idle', guildId: this.guildId });
    }

    if (this.queue.length === 0 && !this.speaking) return; // fully idle: stop the clock

    this.nextTick += 20;
    const now = performance.now();
    if (now - this.nextTick > 100) this.nextTick = now; // resync after stalls
    this.timer = setTimeout(() => this.tick(), Math.max(0, this.nextTick - now));
  }

  destroy(reason = 'leave') {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    for (const { stream } of this.decoders.values()) stream.destroy();
    this.decoders.clear();
    this.queue.clear();
    try {
      if (this.connection && this.connection.state.status !== VoiceConnectionStatus.Destroyed) {
        this.connection.destroy();
      }
    } catch (err) {
      debug('destroy error', err?.message);
    }
    guilds.delete(this.guildId);
    send({ op: 'left', guildId: this.guildId, reason });
  }
}

function handleControl(msg) {
  switch (msg.op) {
    case 'join': {
      const existing = guilds.get(msg.guildId);
      if (existing) {
        if (existing.channelId === msg.channelId) {
          send({ op: 'connection_state', guildId: msg.guildId, state: existing.connection?.state.status });
          return;
        }
        existing.destroy('moved');
      }
      const gv = new GuildVoice(msg.guildId, msg.channelId, msg.selfUserId);
      guilds.set(msg.guildId, gv);
      gv.connect();
      return;
    }
    case 'leave':
      guilds.get(msg.guildId)?.destroy('leave');
      return;
    case 'voice_state':
      guilds.get(msg.guildId)?.onVoiceState(msg.d);
      return;
    case 'voice_server':
      guilds.get(msg.guildId)?.onVoiceServer(msg.d);
      return;
    case 'flush':
      guilds.get(msg.guildId)?.flush();
      return;
    case 'forget_user':
      guilds.get(msg.guildId)?.forgetUser(msg.userId);
      return;
    case 'ping':
      send({ op: 'pong', t: msg.t, guilds: [...guilds.keys()] });
      return;
    default:
      debug('unknown op', msg.op);
  }
}

function tokenMatches(candidate) {
  const a = Buffer.from(String(candidate || ''));
  const b = Buffer.from(TOKEN);
  return a.length === b.length && timingSafeEqual(a, b);
}

const wss = new WebSocketServer({ host: HOST, port: PORT, maxPayload: 4 << 20 });

wss.on('connection', (ws, req) => {
  let authed = false;
  const authTimer = setTimeout(() => !authed && ws.close(4001, 'auth timeout'), 3000);

  ws.on('message', (data, isBinary) => {
    if (!authed) {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.op === 'hello' && tokenMatches(msg.token)) {
          authed = true;
          clearTimeout(authTimer);
          if (bot && bot !== ws) bot.close(4002, 'replaced');
          bot = ws;
          log(`bot connected from ${req.socket.remoteAddress}`);
          send({ op: 'ready', version: 1 });
          return;
        }
      } catch {}
      ws.close(4003, 'unauthorized');
      return;
    }
    if (isBinary) {
      const pb = decodePlayback(data);
      if (pb) guilds.get(pb.guildId)?.enqueue(pb.pcm);
      return;
    }
    try {
      handleControl(JSON.parse(data.toString()));
    } catch (err) {
      log('bad control message:', err?.message);
    }
  });

  ws.on('close', () => {
    clearTimeout(authTimer);
    if (bot === ws) {
      bot = null;
      // The bot owns the gateway. Without it, voice connections can't be kept alive.
      log('bot disconnected; leaving all voice channels');
      for (const gv of [...guilds.values()]) gv.destroy('bot_disconnected');
    }
  });
});

wss.on('listening', () => log(`listening on ws://${HOST}:${PORT}`));
wss.on('error', (err) => {
  console.error('[bridge] server error:', err?.message || err);
  process.exit(1);
});

function shutdown() {
  for (const gv of [...guilds.values()]) gv.destroy('shutdown');
  wss.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

// When the bot started us, exit as soon as it goes away (its end of our stdin closes),
// so a crashed or SIGKILLed bot never leaves an orphaned bridge holding the port.
if (/^(1|true)$/i.test(process.env.NYANJOU_BRIDGE_EXIT_ON_STDIN_CLOSE || '')) {
  process.stdin.on('end', shutdown);
  process.stdin.on('close', shutdown);
  process.stdin.resume();
}

// Exported for tests that want to poke internals without a real Discord connection.
export { getVoiceConnection };
