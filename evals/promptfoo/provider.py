"""promptfoo の Python のプロバイダー。本番の判定のコード（ViolationDetector）で 1 投稿を判定する。

promptfooconfig.yml の providers の config:
  mode: decisions（新方式。既定）／luna（予備経路だけ）／llm_score（旧方式）
  model: 判定のモデル（省略時は mode ごとの既定。decisions・luna は gpt-6-luna、llm_score は gpt-4o-mini）
  prompts_from_head: true なら、プロンプトを git の HEAD の版から読む（既定は false＝作業ツリーの版）

prompt には投稿の本文（{{text}}）が入る。output は JSON の dict（is_violation・article_id・error など。harness.detect）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402


def call_api(prompt: str, options: dict, context: dict) -> dict:
    cfg = (options or {}).get("config", {}) or {}
    mode = cfg.get("mode", "decisions")
    model = cfg.get("model") or None
    result = harness.detect(prompt, mode=mode, model=model, prompts_from_head=bool(cfg.get("prompts_from_head", False)))
    tokens_in = sum(c.get("in", 0) for c in result["calls"])
    tokens_out = sum(c.get("out", 0) for c in result["calls"])
    cached = sum(c.get("cached", 0) or 0 for c in result["calls"])
    resp = {
        "output": result,
        "tokenUsage": {"prompt": tokens_in, "completion": tokens_out, "total": tokens_in + tokens_out, "cached": cached},
        "cost": result["cost_usd"],
    }
    if result["latency_sec"] is not None:
        resp["latencyMs"] = int(result["latency_sec"] * 1000)
    return resp
