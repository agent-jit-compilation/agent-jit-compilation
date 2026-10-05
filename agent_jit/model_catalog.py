"""Provider routing and conservative standard-token rates for paper models."""

import os

OPENAI_SOURCE = "https://developers.openai.com/api/docs/models/"
GOOGLE_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
GROQ_SOURCE = "https://console.groq.com/docs/model/openai/gpt-oss-120b"

# USD / million tokens. Pro uses the higher long-context tier for reservations.
MODELS = {
    "gpt-4.1": ("openai", 2.0, 8.0, OPENAI_SOURCE + "gpt-4.1"),
    "gpt-4.1-mini": ("openai", 0.4, 1.6, OPENAI_SOURCE + "gpt-4.1-mini"),
    "gpt-5": ("openai", 1.25, 10.0, OPENAI_SOURCE + "gpt-5"),
    "gpt-5-mini": ("openai", 0.25, 2.0, OPENAI_SOURCE + "gpt-5-mini"),
    "gemini-2.5-pro": ("google", 2.5, 15.0, GOOGLE_SOURCE),
    "gemini-2.5-flash": ("google", 0.3, 2.5, GOOGLE_SOURCE),
    "gemini-2.0-flash-lite": ("google", 0.075, 0.3, GOOGLE_SOURCE),
    "claude-sonnet-4-20250514": ("anthropic", 3.0, 15.0, ANTHROPIC_SOURCE),
    "openai/gpt-oss-120b": ("groq", 0.15, 0.6, GROQ_SOURCE),
}
MODEL_ALIASES = {
    "gpt-4.1-2025-04-14": "gpt-4.1",
    "gpt-4.1-mini-2025-04-14": "gpt-4.1-mini",
    "gpt-5-2025-08-07": "gpt-5",
    "gpt-5-mini-2025-08-07": "gpt-5-mini",
}
for alias, base in MODEL_ALIASES.items():
    MODELS[alias] = MODELS[base]

RATES = {name: (entry[1], entry[2]) for name, entry in MODELS.items()}


def provider(model):
    if model not in MODELS:
        raise ValueError(f"Unsupported model: {model}. Supported: {', '.join(MODELS)}")
    return MODELS[model][0]


def require_credentials(model):
    names = {
        "openai": ("OPENAI_API_KEY",),
        "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "groq": ("GROQ_API_KEY",),
    }[provider(model)]
    if not any(os.environ.get(name) for name in names):
        raise ValueError(f"{' or '.join(names)} is missing for {model}; load your .env")


def cross_model_profile(config):
    """Whether a JIT run selects schedules using another execution model's profile."""
    if (
        config.get("adapter") != "agent_jit.adapters.scheduler:run"
        or config.get("strategy", "jit") != "jit"
    ):
        return False
    execution = config.get("model")
    profile = config.get("profile_model", "gpt-4.1")
    return MODEL_ALIASES.get(execution, execution) != MODEL_ALIASES.get(
        profile, profile
    )
