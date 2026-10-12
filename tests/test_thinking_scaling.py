import unittest
from transformer.content import calculate_thinking_budget, convert_anthropic_to_google


class TestThinkingScaling(unittest.TestCase):
    def test_default_budgets_small_context(self):
        # <= 100k tokens preserves base budget
        # Claude: default 32000
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 50000), 32000)
        self.assertEqual(calculate_thinking_budget("claude-opus-4-6-thinking", 100000), 32000)

        # Pro: default 16000
        self.assertEqual(calculate_thinking_budget("gemini-2.5-pro", 50000), 16000)
        self.assertEqual(calculate_thinking_budget("gemini-1.5-pro", 100000), 16000)

        # Flash / Other: default 2048
        self.assertEqual(calculate_thinking_budget("gemini-2.5-flash", 50000), 2048)
        self.assertEqual(calculate_thinking_budget("gemini-2.0-flash", 100000), 2048)

    def test_user_budget_small_context(self):
        # User budget preserved when <= 100k
        self.assertEqual(calculate_thinking_budget("gemini-2.5-flash", 50000, user_budget=10000), 10000)
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 80000, user_budget=5000), 5000)

    def test_medium_context_scaling(self):
        # 100_001 to 300_000: clamp to max 8192 (or 2048 for Flash)
        # Claude
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 100001), 8192)
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 250000), 8192)
        # Pro
        self.assertEqual(calculate_thinking_budget("gemini-2.5-pro", 150000), 8192)
        # Flash
        self.assertEqual(calculate_thinking_budget("gemini-2.5-flash", 150000), 2048)

        # If user budget was lower than clamp, user budget remains (floor/ceiling rules)
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 150000, user_budget=4000), 4000)
        # If user budget was higher than clamp, it is clamped
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 150000, user_budget=16000), 8192)
        self.assertEqual(calculate_thinking_budget("gemini-2.5-flash", 150000, user_budget=5000), 2048)

    def test_large_context_scaling(self):
        # > 300_000: clamp to max 4096 for Claude/Pro, and 1024 for Flash
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 300001), 4096)
        self.assertEqual(calculate_thinking_budget("gemini-2.5-pro", 500000), 4096)
        self.assertEqual(calculate_thinking_budget("gemini-2.5-flash", 350000), 1024)

        # If user budget was higher than clamp
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 400000, user_budget=8000), 4096)

    def test_minimum_floor(self):
        # Never drop below 1024 tokens
        self.assertEqual(calculate_thinking_budget("claini-custom", 500000, user_budget=500), 1024)
        self.assertEqual(calculate_thinking_budget("gemini-2.5-flash", 500000, user_budget=200), 1024)

    def test_model_maximum_ceiling(self):
        # Model maximum: respect existing ceiling (e.g. 24576 for 2.5, 64000 for 3.0+)
        # For 2.5 flash/pro with a huge user budget on small context
        self.assertEqual(calculate_thinking_budget("gemini-2.5-pro", 10000, user_budget=30000), 24576)
        # Claude models ceiling
        self.assertEqual(calculate_thinking_budget("claude-3-7-sonnet", 10000, user_budget=70000), 64000)

    def test_integration_in_convert_anthropic_to_google(self):
        # Claude model with thinking requested
        req_small = {
            "model": "claude-3-7-sonnet",
            "thinking": {"type": "enabled", "budget_tokens": 32000},
            "messages": [{"role": "user", "content": "Hello"}]
        }
        res_small = convert_anthropic_to_google(req_small, "test-proj")
        self.assertEqual(res_small["request"]["generationConfig"]["thinkingConfig"]["thinking_budget"], 32000)

        # Large context Claude model
        req_large = {
            "model": "claude-3-7-sonnet",
            "thinking": {"type": "enabled", "budget_tokens": 32000},
            "messages": [{"role": "user", "content": "word " * 120000}]  # > 100k tokens
        }
        res_large = convert_anthropic_to_google(req_large, "test-proj")
        budget_large = res_large["request"]["generationConfig"]["thinkingConfig"]["thinking_budget"]
        self.assertLessEqual(budget_large, 8192)

        # Gemini flash model
        req_flash_large = {
            "model": "gemini-2.5-flash",
            "thinking": {"type": "enabled"},
            "messages": [{"role": "user", "content": "word " * 350000}]  # > 300k tokens
        }
        res_flash_large = convert_anthropic_to_google(req_flash_large, "test-proj")
        self.assertEqual(res_flash_large["request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"], 1024)


if __name__ == "__main__":
    unittest.main()
