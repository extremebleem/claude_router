from typing import Any, Optional, Dict, List

# Model ids advertised by /v1/models for the Zen backend. The opencode/ prefix is what the client
# must send, but Zen itself only ever receives the bare id (see backend.opencode.zen_model_id).
OPENCODE_MODELS = [
    "space-bunny-free",
    "opencode/big-pickle",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/union-alpha",
]

# Bare (prefix-stripped) ids routed to Zen even when the client sends them without the namespace.
OPENCODE_BARE_PREFIXES = ("space-bunny",)


class ProviderRouter:
    def __init__(self, antigravity_client: Any, opencode_client: Any):
        self.antigravity_client = antigravity_client
        self.opencode_client = opencode_client

    def resolve_provider(self, model: str) -> str:
        model_lower = (model or "").strip().lower()
        if model_lower.startswith("opencode/") or model_lower in OPENCODE_MODELS:
            return "opencode"
        # Matched on the bare id and anchored to the start, so an unrelated model that merely
        # mentions space-bunny in its name is not hijacked.
        bare = model_lower.rsplit("/", 1)[-1]
        if bare.startswith(OPENCODE_BARE_PREFIXES):
            return "opencode"
        return "antigravity"

    def resolve_client(self, model: str) -> Any:
        provider = self.resolve_provider(model)
        if provider == "opencode":
            return self.opencode_client
        return self.antigravity_client

    def list_all_models(self, antigravity_models: Optional[List[str]] = None) -> List[str]:
        models = list(antigravity_models or [])
        for m in OPENCODE_MODELS:
            if m not in models:
                models.append(m)
        return models