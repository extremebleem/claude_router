from typing import Any, Optional, Dict, List

from auth.opencode_catalog import free_models, routable_models
from auth.opencode_catalog import peek_catalog as peek

# Advertised when the live catalogue has not been fetched yet. It is a snapshot, not a source of
# truth: /v1/models replaces it with whatever the account can actually reach, and the router
# recognises the live ids once the catalogue is warm.
OPENCODE_MODELS = [
    "space-bunny-free",
    "opencode/big-pickle",
    "opencode/cat-jam-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/ling-3.1-flash-free",
    "opencode/mimo-v2.5-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/longcat-2.5-preview-free",
    "opencode/step-5-preview-free",
]

# Bare ids that belong to Zen even without the namespace. Zen names its sponsored models either
# with a -free suffix or as a stealth release; neither shape collides with an Antigravity id.
FREE_MODEL_SUFFIXES = ("-free",)
FREE_MODEL_IDS = ("big-pickle",)


def is_free_tier_model(model_id: str) -> bool:
    bare = (model_id or "").rsplit("/", 1)[-1].lower()
    return bare.endswith(FREE_MODEL_SUFFIXES) or bare in FREE_MODEL_IDS


def opencode_model_ids(free_only: bool = False) -> List[str]:
    """Ids advertised for Zen: the live catalogue when warm, the snapshot otherwise.

    Without a credential only the free models are reachable, so advertising the paid ones would
    offer the client a model whose every turn fails with AuthError.
    """
    catalog = peek()
    live = routable_models(catalog)
    if free_only:
        reachable = set(free_models(catalog))
        live = [m for m in live if m in reachable]
    if not live:
        return [m for m in OPENCODE_MODELS if not free_only or is_free_tier_model(m)]
    # space-bunny-free stays unprefixed so the common case stays short to type; the rest carry the
    # namespace the client must send, matching how opencode itself names them.
    return [m if m == "space-bunny-free" else f"opencode/{m}" for m in sorted(live)]


class ProviderRouter:
    def __init__(self, antigravity_client: Any, opencode_client: Any, token_manager: Any = None):
        self.antigravity_client = antigravity_client
        self.opencode_client = opencode_client
        self.token_manager = token_manager

    def opencode_free_only(self) -> bool:
        """True when Zen has no credential, so only its free models can actually be served."""
        if self.token_manager is None:
            return True
        try:
            return not self.token_manager.get_accounts(provider="opencode")
        except Exception:
            return True

    def resolve_provider(self, model: str) -> str:
        model_lower = (model or "").strip().lower()
        if model_lower.startswith("opencode/"):
            return "opencode"
        bare = model_lower.rsplit("/", 1)[-1]
        # Bare ids go to Zen only for its free-tier models. Zen also serves claude-sonnet-4-6 and
        # gemini-3.8-flash, and those ids belong to Antigravity in this proxy - claiming them from
        # the catalogue would silently move a Google conversation onto a paid Zen model.
        return "opencode" if is_free_tier_model(bare) else "antigravity"

    def resolve_client(self, model: str) -> Any:
        provider = self.resolve_provider(model)
        if provider == "opencode":
            return self.opencode_client
        return self.antigravity_client

    def list_all_models(self, antigravity_models: Optional[List[str]] = None) -> List[str]:
        models = list(antigravity_models or [])
        for m in opencode_model_ids(free_only=self.opencode_free_only()):
            if m not in models:
                models.append(m)
        return models