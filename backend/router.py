from typing import Any, Optional, Dict, List

OPENCODE_MODELS = [
    "space-bunny-free",
    "opencode/space-bunny-free",
    "opencode/big-pickle",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/union-alpha"
]

class ProviderRouter:
    def __init__(self, antigravity_client: Any, opencode_client: Any):
        self.antigravity_client = antigravity_client
        self.opencode_client = opencode_client

    def resolve_provider(self, model: str) -> str:
        model_lower = (model or "").lower()
        if (
            model_lower.startswith("opencode/")
            or "space-bunny" in model_lower
            or model_lower in OPENCODE_MODELS
        ):
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
