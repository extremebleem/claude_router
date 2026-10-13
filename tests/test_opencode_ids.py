import re
import unittest

from transformer.opencode_ids import (
    ID_ALPHABET,
    ID_LENGTH,
    message_identifier,
    opencode_identifier,
    session_identifier,
)

SESSION_PATTERN = re.compile(r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
MESSAGE_PATTERN = re.compile(r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
# Captured from opencode 1.18.31: sessions mint descending ids, messages ascending ones.
REAL_SESSION = "ses_ec9445605ffeWMqg0qYbNwIFG7"
REAL_MESSAGE = "msg_136bbaa58001UXLn7iLae5zdyN"


class TestOpencodeIdentifier(unittest.TestCase):
    def test_shape_matches_the_client(self):
        for ident in (session_identifier("a")[4:], message_identifier("a")[4:], opencode_identifier()):
            self.assertEqual(len(ident), ID_LENGTH)
            self.assertTrue(all(char in ID_ALPHABET for char in ident))
        self.assertRegex(session_identifier("a"), SESSION_PATTERN)
        self.assertRegex(message_identifier("a"), MESSAGE_PATTERN)

    def test_descending_is_the_complement_of_ascending(self):
        """The counter is packed into the low 12 bits, so ~value turns counter 1 into 0xffe - which
        is why every real session id ends in 'ffe' and every message id in '001'."""
        stamp = 1_700_000_000_000
        self.assertEqual(opencode_identifier(True, 1, stamp, b"\0" * 14)[:12], "4301a97ffffe")
        self.assertEqual(opencode_identifier(False, 1, stamp, b"\0" * 14)[:12], "bcfe56800001")
        self.assertEqual(opencode_identifier(True, 2, stamp, b"\0" * 14)[:12], "4301a97ffffd")

    def test_minted_ids_look_like_the_real_ones(self):
        self.assertRegex(session_identifier("seed"), SESSION_PATTERN)
        self.assertRegex(message_identifier("seed"), MESSAGE_PATTERN)
        # The first id of a millisecond carries counter 1, which reads as ffe descending / 001 ascending.
        self.assertEqual(session_identifier("seed")[4 + 9:4 + 12], "ffe")
        self.assertEqual(message_identifier("seed")[4 + 9:4 + 12], "001")

    def test_message_ids_do_not_disturb_the_conversation_id(self):
        """Message ids are minted per turn and are deliberately not cached, so a busy conversation
        cannot push its own session out of the stamp cache and change the id mid-flight."""
        first = session_identifier("busy")
        for turn in range(2000):
            message_identifier(f"busy:{turn}")
        self.assertEqual(session_identifier("busy"), first)

    def test_the_random_tail_only_uses_the_client_alphabet(self):
        tail = opencode_identifier(random_bytes=bytes(range(14)))[ID_LENGTH - 14:]
        self.assertTrue(all(char in ID_ALPHABET for char in tail))
        self.assertEqual(len(set(tail)), len(tail))

    def test_one_seed_keeps_one_id_and_different_seeds_diverge(self):
        self.assertEqual(session_identifier("same"), session_identifier("same"))
        self.assertNotEqual(session_identifier("a"), session_identifier("b"))
        self.assertNotEqual(message_identifier("same"), session_identifier("same"))

    def test_a_stale_timestamp_still_has_the_shape_the_gateway_wants(self):
        """Verified live: the gateway accepts a day-old stamp, so ids can be minted once per
        conversation instead of per turn, which is what keeps the prompt cache keyed correctly."""
        stale = opencode_identifier(True, 1, 1_690_000_000_000)
        self.assertEqual(len(stale), ID_LENGTH)
        self.assertRegex("ses_" + stale, SESSION_PATTERN)


class TestRealClientIdsAreReproducible(unittest.TestCase):
    """The port is only right if it can rebuild an id the client actually produced.

    The timestamp itself is not recoverable - Date.now() * 4096 needs 53 bits and only 48 are kept -
    which is also why the gateway can only check the shape and not the age of an id.
    """

    def test_a_real_id_decodes_to_its_own_timestamp_and_counter(self):
        modulus = 1 << 48  # the id keeps the low 48 bits of the value
        for real, descending in ((REAL_SESSION, True), (REAL_MESSAGE, False)):
            stored = int(real[4:16], 16)
            current = (~stored & (modulus - 1)) if descending else stored
            counter = current & 0xFFF
            timestamp_ms = current >> 12
            self.assertEqual(counter, 1, real)
            rebuilt = opencode_identifier(descending, counter, timestamp_ms, b"\0" * 14)
            self.assertEqual(rebuilt[:12], real[4:16])


if __name__ == "__main__":
    unittest.main()