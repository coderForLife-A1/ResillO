from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.total_failures = 0
        self.total_successes = 0
        self.trips_count = 0
        self.opened_at = 0.0

    @property
    def state(self) -> str:
        if self.opened_at and time.monotonic() - self.opened_at < self.cooldown:
            return "open"
        return "half-open" if self.opened_at else "closed"

    @property
    def info(self) -> dict[str, Any]:
        cooldown_remaining = max(0.0, self.cooldown - (time.monotonic() - self.opened_at)) if self.opened_at else 0.0
        return {
            "state": self.state,
            "trips": self.trips_count,
            "current_failures": self.failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "cooldown_remaining_seconds": round(cooldown_remaining, 2),
        }

    def success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0
        self.total_successes += 1

    def failure(self) -> None:
        self.failures += 1
        self.total_failures += 1
        if self.failures >= self.threshold:
            if not self.opened_at or time.monotonic() - self.opened_at >= self.cooldown:
                self.trips_count += 1
            self.opened_at = time.monotonic()


def fallback_parse(text: str) -> dict[str, Any]:
    lower = text.lower()
    priority = "CRITICAL" if any(word in lower for word in ("critical", "collapse", "death", "severe")) else "HIGH" if any(word in lower for word in ("urgent", "shortage", "emergency")) else "MEDIUM" if any(word in lower for word in ("injury", "injured", "blocked")) else "LOW"
    category = "Medical" if any(word in lower for word in ("medical", "doctor", "blood", "injur")) else "Supplies" if any(word in lower for word in ("supplies", "food", "water", "generator")) else "Infrastructure" if any(word in lower for word in ("bridge", "road", "power", "grid", "collapse")) else "Status Update"
    return {"priority": priority, "category": category, "location": "Unknown", "summary": text[:100] + ("..." if len(text) > 100 else ""), "actionable_needs": ["Review manually"], "status": "fallback_mode"}


class TriageEngine:
    def __init__(self, url: str, model: str, connect_timeout: float, read_timeout: float) -> None:
        self.url = url
        self.model = model
        self.timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self.breaker = CircuitBreaker()

    async def classify(self, text: str) -> dict[str, Any]:
        if self.breaker.state == "open":
            return fallback_parse(text)
        prompt = "Return JSON with priority (CRITICAL/HIGH/MEDIUM/LOW), category (Medical/Supplies/Infrastructure/Status Update), location, summary, actionable_needs. Raw note: " + text
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.url}/api/generate", json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"})
                response.raise_for_status()
                parsed = json.loads(response.json().get("response", "{}"))
                if parsed.get("priority") not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} or parsed.get("category") not in {"Medical", "Supplies", "Infrastructure", "Status Update"} or not isinstance(parsed.get("actionable_needs"), list):
                    raise ValueError("Ollama returned an invalid triage schema")
                self.breaker.success()
                return parsed
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, asyncio.TimeoutError):
            self.breaker.failure()
            return fallback_parse(text)
