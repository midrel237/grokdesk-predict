from __future__ import annotations

import hashlib
import os


class LLM:
    """Thin wrapper around a Grok / OpenAI-compatible endpoint.

    When no API key is present it falls back to a deterministic offline
    heuristic, so the whole desk runs in paper mode with zero network calls.
    Set XAI_API_KEY (or OPENAI_API_KEY) and wire `_remote_score` to go live.
    """

    def __init__(self, model: str = "grok-2-latest") -> None:
        self.model = model
        self.key = os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.online = bool(self.key)

    def score_narrative(self, symbol: str, blurb: str) -> float:
        """Return 0..1 narrative strength for a fresh mint."""
        if self.online:
            try:
                return self._remote_score(symbol, blurb)
            except Exception:
                pass
        h = int(hashlib.sha256(f"{symbol}|{blurb}".encode()).hexdigest(), 16)
        return (h % 1000) / 1000.0

    def _remote_score(self, symbol: str, blurb: str) -> float:
        # Wire your provider here. Kept intentionally minimal; the desk is
        # designed to run and be judged in paper mode first.
        import json
        import urllib.request

        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Score memecoin narrative 0..1. Reply with only the number."},
                {"role": "user", "content": f"{symbol}: {blurb}"},
            ],
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            os.getenv("LLM_BASE", "https://api.x.ai/v1") + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read())
        txt = out["choices"][0]["message"]["content"].strip()
        return max(0.0, min(1.0, float(txt)))
