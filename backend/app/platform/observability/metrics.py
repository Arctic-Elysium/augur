from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

# Domain metrics. HTTP metrics come free from the instrumentator.
ai_requests = Counter(
    "augur_ai_requests_total",
    "AI gateway requests",
    ["capability", "backend", "outcome"],
)
ai_tokens = Counter(
    "augur_ai_tokens_total",
    "Tokens consumed",
    ["capability", "backend", "direction"],
)
ai_latency = Histogram(
    "augur_ai_latency_seconds",
    "AI gateway latency",
    ["capability", "backend"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
turns_resolved = Counter(
    "augur_turns_total",
    "Game turns resolved",
    ["play_mode", "outcome"],
)
rule_violations = Counter(
    "augur_rule_violations_total",
    "Actions rejected by the rules engine",
    ["source"],  # player | model
)


def install_metrics(app: FastAPI) -> None:
    Instrumentator().instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )
