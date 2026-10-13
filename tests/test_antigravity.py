import unittest
from backend.antigravity import AntigravityClient

class TestAntigravity(unittest.TestCase):
    def test_antigravity_client_instantiation(self):
        client = AntigravityClient(token_manager=None, project_id="test-proj")
        self.assertEqual(client.project_id, "test-proj")
        self.assertTrue(hasattr(client, "generate_stream"))

if __name__ == "__main__":
    unittest.main()
