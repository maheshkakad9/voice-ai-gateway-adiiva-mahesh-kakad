# Voice AI Gateway
High-concurrency real-time Voice AI Gateway built using the Pipecat Quickstart Architecture.
Supports multiple users, JWT authentication, Redis-based concurrency limits, live metrics, and real-time AI voice conversations.

Built for the ADIIVA Backend AI Engineer assignment.

Tech Stack

| Component      | Service                                      |
| -------------- | -------------------------------------------- |
| Backend        | FastAPI                                      |
| Voice Pipeline | Pipecat                                      |
| STT            | Deepgram                                     |
| LLM            | Groq (OpenAI-compatible)                     |
| TTS            | Cartesia                                     |
| Auth           | JWT                                          |
| Rate Limiting  | Redis                                        |
| Metrics        | Prometheus                                   |
| Logging        | Structlog                                    |

## Features

- Real-time voice conversations over WebSockets
- JWT authentication
- Redis-backed concurrency control
- Multi-user session management
- Real-time usage tracking
- Cost estimation per session
- Prometheus metrics
- Structured logging
- Dockerized deployment
- Supports concurrent users without blocking FastAPI event loop

## Quick Start
 
```bash
# 1. Clone the repository
git clone https://github.com/maheshkakad9/voice-ai-gateway-adiiva-mahesh-kakad.git
cd voice-ai-gateway
 
# 2. Configure environment variables
cp .env.example .env
# Edit .env — add GROQ_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY, JWT_SECRET_KEY
 
# 3. Start everything with one command
docker compose up --build
```
 
## API Provider

This project is configured to work with the **Groq API only** for LLM inference.

> Gemini and OpenAI were previously used during development, but their credits were exhausted, so the current deployment uses only Groq.

Make sure `GROQ_API_KEY` is set in your `.env` file before starting the project.

| Service | URL |
|---------|-----|
| Frontend | http://localhost |
| Backend API | http://localhost:8000 |
| Metrics | http://localhost:8000/metrics |
| Health | http://localhost:8000/health |
 
> **No separate frontend setup required.** The React app is built inside Docker and served via nginx on port 80

## Authentication

### Get a token

```bash
curl -s -X POST http://localhost:8000/token \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"password123"}' | jq
```

Demo users: `alice/password123` · `bob/password456` · `charlie/password789`

### JWT rejection demo

```bash
# No token → 401
curl http://localhost:8000/sessions

# Bad token → 401
curl -H "Authorization: Bearer BADTOKEN" http://localhost:8000/sessions

# WebSocket with bad token → 401 on upgrade
wscat -c "ws://localhost:8000/ws/talk?token=BADTOKEN"
```

---

## WebSocket Usage

```
ws://localhost:8000/ws/talk?token=<jwt>
```

| Direction | Frame | Content |
|-----------|-------|---------|
| Client → Server | Binary | Raw PCM audio (16-bit, 16 kHz, mono) |
| Server → Client | Binary | TTS audio (WAV chunks, 24 kHz) |
| Server → Client | Text | JSON events (transcript, usage, errors) |

### Connect with wscat

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/token \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"password123"}' | jq -r .access_token)

wscat -c "ws://localhost:8000/ws/talk?token=$TOKEN"
```

---

## Redis Rate Limiting

Enforces `MAX_CONCURRENT_CALLS_PER_USER` (default: 2) active sessions per user.

**How it works:**
- A single atomic Lua script does `GET` + compare + `INCR` in one Redis round-trip (no TOCTOU race)
- Counter key: `vgw:concurrency:<user_id>` with a 1-hour safety TTL
- On disconnect, a second Lua script safely decrements (never below zero)
- On breach: WebSocket closed with code **4001 (Policy Violation)**

### Trigger the rate limiter

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/token \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"password123"}' | jq -r .access_token)

wscat -c "ws://localhost:8000/ws/talk?token=$TOKEN" &
wscat -c "ws://localhost:8000/ws/talk?token=$TOKEN" &
# Third connection → close code 4001
wscat -c "ws://localhost:8000/ws/talk?token=$TOKEN"
```

---

## Metrics

```bash
curl -s http://localhost:8000/metrics | grep vgw_
```
| Metric | Type | Description |
|--------|------|-------------|
| `vgw_active_sessions` | Gauge | Live sessions |
| `vgw_active_websockets` | Gauge | Open WS connections |
| `vgw_e2e_latency_seconds` | Histogram | Speech end → first TTS byte |
| `vgw_llm_latency_seconds` | Histogram | Groq first-token latency |
| `vgw_tts_latency_seconds` | Histogram | Cartesia first-chunk latency |
| `vgw_rate_limit_violations_total` | Counter | 4001 breaches by user |
| `vgw_jwt_rejections_total` | Counter | Auth failures by reason |
| `vgw_llm_input_tokens_total` | Counter | Groq input tokens |
| `vgw_llm_output_tokens_total` | Counter | Groq output tokens |
| `vgw_tts_characters_total` | Counter | Cartesia characters |
| `vgw_estimated_cost_usd_total` | Counter | Cost by user + provider |

## Cost Estimation

| Provider | Rate |
|----------|------|
| Deepgram | $0.0001 / second |
| Groq — Llama 3.3 70B | $0.00000059 / input token · $0.00000079 / output token |
| Groq — Llama 3.1 8B | $0.00000005 / input token · $0.00000008 / output token |
| Cartesia | $0.09 / 1K characters |

## Architecture

```
Client (Browser)
  │  ws://host/ws/talk?token=<jwt>
  │  Binary PCM Audio ↑   WAV Audio + JSON Events ↓
  ▼
FastAPI  (/token  /ws/talk  /metrics  /health  /sessions)
  │
  ├── JWT Authentication
  │     └── python-jose HS256 validation (~0.1 ms)
  │
  ├── Redis Rate Limiter
  │     └── Atomic Lua scripts for concurrency control (~1 ms)
  │
  └── SessionManager
        └── asyncio.Task per active session
              └── Pipecat Pipeline
                    ├── FastAPIWebsocketTransport.input()
                    │     └── Receives streaming PCM audio
                    │
                    ├── DeepgramSTTService
                    │     └── Speech → Text transcription
                    │
                    ├── Context Aggregator (User)
                    │     └── Maintains conversation context
                    │
                    ├── GroqLLMService
                    │     ├── Groq LLM inference
                    │     ├── Tool calling support
                    │     ├── Low-latency token streaming
                    │     └── play_audio Tool
                    │           └── Sends immediate binary frames
                    │
                    ├── CartesiaTTSService
                    │     └── Text → Speech audio generation
                    │
                    ├── FastAPIWebsocketTransport.output()
                    │     └── Streams TTS audio to client
                    │
                    └── Context Aggregator (Assistant)
                          └── Stores assistant responses
```

The architecture is designed for low-latency real-time voice conversations.
FastAPI handles WebSocket communication and session orchestration, while
Pipecat manages the streaming AI pipeline for STT, LLM inference, tool
execution, and TTS generation.



### 160ms Session Synchronization Throttle

The assignment requires a mandatory `time.sleep(0.16)` delay to prevent race conditions during the session loop.

Directly using `time.sleep()` inside an async FastAPI application would block the event loop and reduce concurrency.  
To avoid blocking other active users, the delay is executed inside a separate thread using `asyncio.to_thread()`.

```python
def _blocking_throttle() -> None:
    # Mandatory synchronization delay
    time.sleep(0.16)

async def _async_throttle() -> None:
    # Run blocking sleep in a worker thread
    await asyncio.to_thread(_blocking_throttle)


## Scaling Strategy (5,000+ Concurrent Users)

The current setup runs on a single FastAPI server and can handle a limited number of concurrent voice sessions.

To support thousands of real-time users, the system can be horizontally scaled using separate Gateway and Worker nodes.

```text
                Load Balancer
                       │
        ┌──────────────┴──────────────┐
        │                             │
   Gateway Node 1               Gateway Node 2
   (FastAPI + WebSocket)        (FastAPI + WebSocket)
        │                             │
        └──────────────┬──────────────┘
                       │
                Redis / Queue Layer
                       │
        ┌──────────────┴──────────────┐
        │                             │
    Worker Node 1                Worker Node 2
   (Pipecat Pipeline)           (Pipecat Pipeline)

## Gateway Nodes

Gateway nodes are lightweight FastAPI servers responsible for:
JWT authentication
WebSocket connection handling
Redis-based rate limiting
Session routing
These nodes are stateless and can be scaled easily by adding more replicas behind a load balancer.

## Worker Nodes
Worker nodes run the actual Pipecat voice pipelines:
Speech-to-Text (STT)
LLM inference
Tool execution
Text-to-Speech (TTS)
Since voice processing is CPU and network intensive, separating workers from gateways improves stability and scalability.

## Redis / Queue Layer

Redis is used for:

- Shared concurrency tracking
- Distributed session coordination
- Rate limiting
- Session dispatching between gateway and worker nodes
This ensures all servers share the same real-time state.

## Why this architecture works well

Supports horizontal scaling
Prevents FastAPI gateways from becoming overloaded
Isolates heavy AI processing from connection handling
Improves fault tolerance
Better suited for long-lived WebSocket connections
Can scale to thousands of concurrent voice sessions

Sticky WebSocket routing can also be used to keep a user connected to the same gateway node during an active session.

## Resilience Strategy

The system is designed to handle failures gracefully without crashing active voice sessions.

### Failure Handling

- STT failure → skip the current transcription turn and continue listening
- LLM failure → return an error event while keeping the session alive
- TTS failure → skip audio response generation without disconnecting the client
- Redis failure → use a fail-open approach to avoid blocking valid users

---

### Circuit Breaker Design

For production-scale deployments, each external AI provider can use a Circuit Breaker pattern.

```text
CLOSED      → Requests work normally
OPEN        → Temporarily block requests after repeated failures
HALF-OPEN   → Allow limited test requests to check recovery


## Performance Optimizations

To reduce latency during authentication and session setup, the system optimizes both JWT validation and Redis operations.

### JWT Optimization

- Uses lightweight HS256 token verification
- JWT validation happens only once during WebSocket upgrade
- Application settings are cached using `@lru_cache`

This avoids repeated authentication overhead during active streaming sessions.

---

### Redis Optimization

- Atomic Lua scripts perform concurrency check + increment in a single operation
- Redis connection pooling avoids repeated connection handshakes
- Shared Redis state supports distributed concurrency tracking
- Session cleanup runs asynchronously on disconnect

These optimizations help maintain low latency and stable performance under concurrent load.

## Demo Walkthrough

The demo video should include:

1. Invalid JWT token rejection
2. Successful user authentication
3. Multiple users connected simultaneously
4. Redis concurrency limit enforcement
5. Live metrics from the `/metrics` endpoint
6. Real-time voice interaction with the AI assistant

---

## Demo Video

```text
[I will upload the demo video link soon]
