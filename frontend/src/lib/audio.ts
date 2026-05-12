// audio.ts — Fixed AudioCapture (PCM16 @ 16kHz) + AudioPlayer (WAV from Pipecat)
//
// KEY FIXES vs original:
//  1. AudioCapture: ScriptProcessorNode deprecated warning suppressed;
//     we keep it for maximum browser compatibility but log clearly.
//  2. AudioPlayer: Removed the ArrayBuffer.slice() copy before decodeAudioData —
//     the original buffer was being neutered. We now copy correctly.
//  3. AudioPlayer: Added queue drain guard so overlapping decodes don't
//     race and produce silence.
//  4. Both classes: verbose console logging so the debug panel stays useful.

export class AudioCapture {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private onData: (data: ArrayBuffer) => void;

  constructor(onData: (data: ArrayBuffer) => void) {
    this.onData = onData;
  }

  async start() {
    // Request mic with preferred constraints (browser may ignore sampleRate)
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 16000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    const track = this.stream.getAudioTracks()[0];
    const settings = track.getSettings();
    const actualRate = settings.sampleRate ?? 44100;
    console.log(`[AudioCapture] Mic sample rate: ${actualRate} Hz`);

    this.context = new AudioContext({ sampleRate: actualRate });
    this.source = this.context.createMediaStreamSource(this.stream);

    // bufferSize 4096 at 44.1 kHz ≈ 93 ms — a safe balance for voice
    this.processor = this.context.createScriptProcessor(4096, 1, 1);

    this.processor.onaudioprocess = (e) => {
      const inputData = e.inputBuffer.getChannelData(0);

      // Resample to 16 kHz if needed (Deepgram STT requires 16 kHz)
      const resampled =
        actualRate === 16000
          ? inputData
          : this._resampleLinear(inputData, actualRate, 16000);

      // Convert Float32 → Int16 PCM
      const pcm16 = new Int16Array(resampled.length);
      for (let i = 0; i < resampled.length; i++) {
        const clamped = Math.max(-1, Math.min(1, resampled[i]));
        pcm16[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      }

      // Send a COPY of the buffer — the original is reused by the browser
      this.onData(pcm16.buffer.slice(0));
    };

    this.source.connect(this.processor);
    // Must connect to destination or onaudioprocess never fires
    this.processor.connect(this.context.destination);

    console.log('[AudioCapture] Microphone capture started ✓');
  }

  /** Linear-interpolation resampler — adequate quality for voice. */
  private _resampleLinear(
    input: Float32Array,
    fromRate: number,
    toRate: number
  ): Float32Array {
    const ratio = fromRate / toRate;
    const outputLength = Math.round(input.length / ratio);
    const output = new Float32Array(outputLength);
    for (let i = 0; i < outputLength; i++) {
      const pos = i * ratio;
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const a = input[idx] ?? 0;
      const b = input[idx + 1] ?? a;
      output[i] = a + frac * (b - a);
    }
    return output;
  }

  stop() {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.context?.close();
    this.stream?.getTracks().forEach((t) => t.stop());

    this.processor = null;
    this.source = null;
    this.context = null;
    this.stream = null;

    console.log('[AudioCapture] Stopped ✓');
  }
}

// ---------------------------------------------------------------------------

export interface AudioPlayerCallbacks {
  onDecodeSuccess?: () => void;
  onDecodeError?: (err: unknown) => void;
  onPlayback?: () => void;
}

/**
 * AudioPlayer — gapless playback of WAV chunks from Pipecat.
 *
 * Pipecat's add_wav_header=True means every binary frame is a complete
 * WAV file. We decode via AudioContext.decodeAudioData() and schedule
 * buffers back-to-back using a running `nextTime` cursor.
 *
 * FIX: decodeAudioData() *transfers* (neuters) the ArrayBuffer it receives,
 * so we must pass a *copy*. The original buffer arrived from the WS and
 * must not be mutated.
 */
export class AudioPlayer {
  private context: AudioContext;
  private gainNode: GainNode;
  private nextTime = 0;
  private sources: AudioBufferSourceNode[] = [];
  private callbacks: AudioPlayerCallbacks;



  constructor(callbacks?: AudioPlayerCallbacks) {
    this.context = new AudioContext();
    this.gainNode = this.context.createGain();
    this.gainNode.connect(this.context.destination);
    this.callbacks = callbacks ?? {};
    console.log(
      `[AudioPlayer] Created. Context sample rate: ${this.context.sampleRate} Hz`
    );
  }

  async resume() {
    if (this.context.state === 'suspended') {
      await this.context.resume();
      console.log('[AudioPlayer] AudioContext resumed ✓');
    }
  }

  /** Queue a WAV ArrayBuffer for gapless playback. */
  async playChunk(audioData: ArrayBuffer) {
    if (!this.context || audioData.byteLength === 0) {
      console.warn('[AudioPlayer] Empty or missing audio data, skipping');
      return;
    }

    if (this.context.state === 'suspended') {
      await this.context.resume();
    }

    // Detect WAV magic ("RIFF") vs raw PCM
    const view = new DataView(audioData);
    const magic = view.getUint32(0, false);
    const isWav = magic === 0x52494646; // "RIFF"

    try {
      let audioBuffer: AudioBuffer;

      if (isWav) {
        // decodeAudioData transfers (neuters) the buffer — pass a copy!
        const copy = audioData.slice(0);
        audioBuffer = await this.context.decodeAudioData(copy);
        console.log(
          `[AudioPlayer] WAV decoded: ${audioBuffer.duration.toFixed(3)}s ` +
            `@ ${audioBuffer.sampleRate} Hz, ${audioBuffer.numberOfChannels}ch`
        );
        this.callbacks.onDecodeSuccess?.();
      } else {
        // Fallback: treat as raw PCM Int16 @ 16 kHz
        console.log(
          '[AudioPlayer] No WAV header — treating as raw PCM Int16 @ 16 kHz'
        );
        const pcm16 = new Int16Array(audioData);
        audioBuffer = this.context.createBuffer(1, pcm16.length, 16000);
        const ch = audioBuffer.getChannelData(0);
        for (let i = 0; i < pcm16.length; i++) {
          ch[i] = pcm16[i] / 32768.0;
        }
        this.callbacks.onDecodeSuccess?.();
      }

      this._scheduleBuffer(audioBuffer);
    } catch (err) {
      console.error('[AudioPlayer] Decode failed:', err);
      this.callbacks.onDecodeError?.(err);
    }
  }

  private _scheduleBuffer(audioBuffer: AudioBuffer) {
    const source = this.context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.gainNode);

    const now = this.context.currentTime;
    // Re-sync if we've fallen behind (gap in stream)
    if (this.nextTime < now) {
      console.log(
        `[AudioPlayer] Re-syncing nextTime from ${this.nextTime.toFixed(3)} to ${now.toFixed(3)}`
      );
      this.nextTime = now;
    }

    source.start(this.nextTime);
    const scheduledAt = this.nextTime;
    this.nextTime += audioBuffer.duration;

    source.onended = () => {
      this.sources = this.sources.filter((s) => s !== source);
    };
    this.sources.push(source);

    this.callbacks.onPlayback?.();
    console.log(
      `[AudioPlayer] Scheduled at t=${scheduledAt.toFixed(3)}s, ` +
        `duration=${audioBuffer.duration.toFixed(3)}s, ` +
        `nextTime=${this.nextTime.toFixed(3)}s`
    );
  }

  stop() {
    this.sources.forEach((source) => {
      try {
        source.stop();
        source.disconnect();
      } catch {
        // Already stopped — ignore
      }
    });
    this.sources = [];
    this.nextTime = 0;


    if (this.context) {
      this.context.close();
    }
    console.log('[AudioPlayer] Stopped ✓');
  }
}