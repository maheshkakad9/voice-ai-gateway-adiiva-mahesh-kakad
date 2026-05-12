// websocket.ts — Fixed for Pipecat FastAPIWebsocketTransport binary framing
//
// Pipecat's FastAPIWebsocketTransport sends audio as raw binary frames
// (ArrayBuffer). JSON text frames carry events like transcripts.
// This client handles both correctly and emits transcript events.

export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface ServerEvent {
  type: string;
  text?: string;
  role?: 'user' | 'assistant';
  [key: string]: any;
}

export class VoiceClient {
  private ws: WebSocket | null = null;
  private token: string;
  private onStatusChange: (status: WsStatus) => void;
  private onAudioReceived: (data: ArrayBuffer) => void;
  private onEventReceived: (event: ServerEvent) => void;

  constructor(
    token: string,
    onStatusChange: (status: WsStatus) => void,
    onAudioReceived: (data: ArrayBuffer) => void,
    onEventReceived: (event: ServerEvent) => void
  ) {
    this.token = token;
    this.onStatusChange = onStatusChange;
    this.onAudioReceived = onAudioReceived;
    this.onEventReceived = onEventReceived;
  }

  connect() {
    this.onStatusChange('connecting');

    // Use the correct Pipecat WebSocket endpoint
    this.ws = new WebSocket(`ws://localhost:8000/ws/talk?token=${this.token}`);

    // CRITICAL: must be 'arraybuffer' so binary frames arrive as ArrayBuffer
    // not Blob — decodeAudioData works better with ArrayBuffer directly.
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      console.log('[VoiceClient] WebSocket connected');
      this.onStatusChange('connected');
    };

    this.ws.onclose = (event) => {
      console.log(`[VoiceClient] WebSocket closed: code=${event.code}, reason=${event.reason}`);
      this.onStatusChange('disconnected');
      this.ws = null;
    };

    this.ws.onerror = (err) => {
      console.error('[VoiceClient] WebSocket error:', err);
      this.onStatusChange('error');
    };

    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame — audio from Pipecat TTS (WAV with header)
        const byteLength = event.data.byteLength;
        if (byteLength === 0) {
          console.warn('[VoiceClient] Received empty binary frame, skipping');
          return;
        }

        // Verify it looks like a WAV (starts with "RIFF")
        const view = new DataView(event.data);
        const magic = view.getUint32(0, false);
        const isWav = magic === 0x52494646; // "RIFF"
        console.log(
          `[VoiceClient] Binary frame: ${byteLength} bytes, isWAV=${isWav}`
        );

        this.onAudioReceived(event.data);

      } else if (typeof event.data === 'string') {
        // Text frame — JSON event from Pipecat (transcripts, metrics, etc.)
        try {
          const parsed = JSON.parse(event.data);
          console.log('[VoiceClient] JSON event:', parsed);
          this.onEventReceived(parsed);

          // Pipecat sends transcription results as:
          // { type: "transcription", text: "...", role: "user" }
          // and LLM responses as:
          // { type: "llm-text-chunk", text: "..." }  (streamed) or
          // { type: "bot-tts-text", text: "..." }
          // We normalise them into a unified transcript event upstream.
        } catch (e) {
          console.warn('[VoiceClient] Non-JSON text message:', event.data);
          // Treat as plain text transcript from assistant
          this.onEventReceived({ type: 'raw-text', text: event.data });
        }
      } else {
        console.warn('[VoiceClient] Unknown message type:', typeof event.data);
      }
    };
  }

  /**
   * Send raw PCM16 mono 16kHz audio to the server.
   * Pipecat's FastAPIWebsocketTransport expects raw PCM (not WAV-wrapped).
   */
  sendAudio(pcm16Data: ArrayBuffer) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(pcm16Data);
    }
  }

  disconnect() {
    if (this.ws) {
      this.ws.close(1000, 'User ended call');
      this.ws = null;
    }
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }
}