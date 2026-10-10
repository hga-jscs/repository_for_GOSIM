"""Resolve Flash aliases consistently for the official API and competition gateway."""

import typer


OFFICIAL_ENDPOINTS = {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
FLASH_MODELS = {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}


def canonical_model(model: str) -> str:
    return "deepseek-flash" if model in FLASH_MODELS else model


def resolve_model(endpoint: str, requested: str | None, *, evolution: bool = False) -> str:
    model = requested or ("deepseek-flash" if endpoint.rstrip("/") in OFFICIAL_ENDPOINTS else "deepseek-v4-flash")
    if evolution and model not in FLASH_MODELS:
        raise typer.BadParameter(
            "Evolution tasks require a DeepSeek Flash model; Pro and other model overrides are disabled. "
            "Use deepseek-flash for the official API or the gateway-provided Flash identifier."
        )
    return canonical_model(model) if endpoint.rstrip("/") in OFFICIAL_ENDPOINTS else model
