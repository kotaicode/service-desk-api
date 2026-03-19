"""Sole place that builds CrewAI LLM from env (tech spec §10)."""
from __future__ import annotations

import os

from crewai import LLM


def get_llm() -> LLM:
    model = os.environ.get("OPENAI_MODEL_NAME", "gpt-4o-mini")
    temperature = float(os.environ.get("OPENAI_TEMPERATURE", "0.2"))
    return LLM(model=model, temperature=temperature)
