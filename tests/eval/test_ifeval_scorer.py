import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ifeval_scorer


class TestScorerRules(unittest.TestCase):
    """The rules are re-implemented, so they are pinned here: a passing/failing response per type."""

    CASES = [
        ("punctuation:no_comma", {}, "no commas here", True),
        ("punctuation:no_comma", {}, "has, comma", False),
        ("startend:quotation", {}, '"quoted text"', True),
        ("startend:quotation", {}, "quoted text", False),
        ("change_case:english_lowercase", {}, "all lower", True),
        ("change_case:english_capital", {}, "ALL CAPS", True),
        ("detectable_format:json_format", {}, '{"a": 1}', True),
        ("detectable_format:json_format", {}, 'here: {"a": 1}', False),
        ("detectable_format:title", {}, "<<My Title>>\nbody", True),
        ("combination:two_responses", {}, "one ****** two", True),
        ("combination:two_responses", {}, "only one", False),
        ("length_constraints:number_paragraphs", {"num_paragraphs": 2}, "first *** second", True),
        ("length_constraints:number_paragraphs", {"num_paragraphs": 3}, "first *** second", False),
        ("length_constraints:number_words", {"num_words": 5}, "one two three four five", True),
        ("length_constraints:number_words", {"num_words": 50}, "short", False),
        ("length_constraints:number_words", {"num_words": 5, "relation": "less than"}, "short", True),
        ("keywords:forbidden_words", {"forbidden_words": ["banana"]}, "this has banana", False),
        ("keywords:existence", {"keywords": ["zelda", "hyrule"]}, "Zelda and Hyrule", True),
        ("keywords:existence", {"keywords": ["zelda", "hyrule"]}, "Zelda only", False),
        ("keywords:frequency", {"keyword": "cat", "frequency": 2, "relation": "at least"},
         "cat and cat", True),
        ("keywords:letter_frequency", {"letter": "z", "let_frequency": 2, "let_relation": "at least"},
         "zebra zoo", True),
        ("detectable_format:number_bullet_lists", {"num_bullets": 3}, "- a\n- b\n- c", True),
        ("detectable_format:number_bullet_lists", {"num_bullets": 3}, "- a\n- b", False),
        ("detectable_format:number_highlighted_sections", {"num_highlights": 2},
         "*one* and *two*", True),
        ("length_constraints:nth_paragraph_first_word", {"nth_paragraph": 2, "first_word": "bonding"},
         "intro *** bonding moment", True),
        ("change_case:capital_word_frequency", {"capital_frequency": 2, "capital_relation": "at least"},
         "THIS IS SHOUTED", True),
        ("detectable_content:postscript", {"postscript_marker": "P.S."}, "text P.S. more", True),
        ("detectable_content:number_placeholders", {"num_placeholders": 2}, "[a] and [b]", True),
        ("startend:end_checker", {"end_phrase": "Thanks."}, "Done. Thanks.", True),
        ("combination:repeat_prompt", {"prompt_to_repeat": "Write a poem"}, "Write a poem about rain", True),
        ("detectable_format:multiple_sections", {"section_spliter": "Section", "num_sections": 2},
         "a Section b Section c", True),
    ]

    def test_every_rule(self):
        for instruction_id, kwargs, response, expected in self.CASES:
            with self.subTest(instruction_id=instruction_id, response=response[:20]):
                self.assertEqual(ifeval_scorer._check(instruction_id, response, kwargs), expected)

    def test_the_cached_dataset_loads_and_covers_every_family(self):
        rows = ifeval_scorer.load_prompts()
        self.assertGreater(len(rows), 500)
        families = {i.split(":")[0] for r in rows for i in r["instruction_id_list"]}
        self.assertGreaterEqual(len(families), 8)

    def test_skipped_instructions_never_count_against_the_model(self):
        rows = ifeval_scorer.load_prompts()
        row = next(r for r in rows if "language:response_language" in r["instruction_id_list"])
        verdicts, ids = ifeval_scorer.score(row, "bonjour")
        self.assertTrue(verdicts[ids.index("language:response_language")])


if __name__ == "__main__":
    unittest.main()