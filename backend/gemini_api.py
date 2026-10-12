"""
Async Client for Direct Google AI Studio Gemini API.
Used when the user provides a standalone GEMINI_API_KEY.
"""

import aiohttp
from typing import Dict, Any, Tuple

from transformer.content import convert_anthropic_to_google


class GeminiApiClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def generate_content_stream(
        self,
        session: aiohttp.ClientSession,
        anthropic_request: Dict[str, Any]
    ) -> Tuple[aiohttp.ClientResponse, str]:
        """Sends request directly to generativelanguage.googleapis.com."""
        requested_model = anthropic_request.get("model", "gemini-2.5-pro")

        # Map Claude aliases to available Gemini models in AI Studio
        model_map = {
            "claude-opus-4-6-thinking": "gemini-2.5-pro",
            "claude-sonnet-4-6-thinking": "gemini-2.5-flash",
            "claude-sonnet-4-6": "gemini-2.5-flash",
        }
        gemini_model = model_map.get(requested_model, requested_model)
        # Strip any thinking suffixes if direct model
        if gemini_model.startswith("gemini-") and gemini_model.endswith("-thinking"):
            gemini_model = gemini_model.replace("-thinking", "")

        payload_wrapper = convert_anthropic_to_google(anthropic_request, project_id="")
        # Direct Gemini API expects just the request contents/generationConfig, not the Antigravity wrapper
        google_body = payload_wrapper.get("request", {})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:streamGenerateContent?alt=sse"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        resp = await session.post(
            url,
            headers=headers,
            json=google_body,
            timeout=aiohttp.ClientTimeout(total=180, sock_read=90)
        )

        if resp.status != 200:
            err_text = await resp.text()
            resp.close()
            raise RuntimeError(f"Google Gemini API error ({resp.status}): {err_text}")

        return resp, requested_model
