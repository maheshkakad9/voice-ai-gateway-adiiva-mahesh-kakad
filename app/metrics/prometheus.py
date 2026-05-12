from __future__ import annotations
from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY, CONTENT_TYPE_LATEST

# Gauges
ACTIVE_SESSIONS = Gauge("vgw_active_sessions", "Currently active voice sessions")
ACTIVE_WS = Gauge("vgw_active_websockets", "Currently open WebSocket connections")

# Counters
WS_TOTAL = Counter("vgw_ws_connections_total", "Total WebSocket connections created")
WS_DISCONNECTIONS = Counter("vgw_ws_disconnections_total", "Total disconnections", ["reason"])
RATE_LIMIT_VIOLATIONS = Counter("vgw_rate_limit_violations_total", "Rate limit breaches", ["user_id"])
JWT_REJECTIONS = Counter("vgw_jwt_rejections_total", "JWT auth rejections", ["reason"])
STT_SECONDS = Counter("vgw_stt_seconds_total", "STT audio seconds processed", ["user_id"])
LLM_INPUT_TOKENS = Counter("vgw_llm_input_tokens_total", "LLM input tokens", ["user_id"])
LLM_OUTPUT_TOKENS = Counter("vgw_llm_output_tokens_total", "LLM output tokens", ["user_id"])
TTS_CHARS = Counter("vgw_tts_characters_total", "TTS characters synthesized", ["user_id"])
COST_USD = Counter("vgw_estimated_cost_usd_total", "Estimated cost USD", ["user_id", "provider"])

# Histograms
E2E_LATENCY = Histogram(
    "vgw_e2e_latency_seconds",
    "End-to-end latency: speech end → first TTS audio byte",
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0],
)
LLM_LATENCY = Histogram(
    "vgw_llm_latency_seconds",
    "LLM first-token latency",
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0],
)
TTS_LATENCY = Histogram(
    "vgw_tts_latency_seconds",
    "TTS first-audio-chunk latency",
    buckets=[0.05, 0.1, 0.2, 0.3, 0.5, 1.0],
)


def get_metrics_output() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
