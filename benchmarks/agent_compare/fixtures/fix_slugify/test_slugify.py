import unittest

from slugify import slugify


class SlugifyTests(unittest.TestCase):
    def test_collapses_whitespace_and_lowercases(self) -> None:
        self.assertEqual(slugify(" Hello   World "), "hello-world")

    def test_preserves_existing_separator(self) -> None:
        self.assertEqual(slugify("Already-Clean"), "already-clean")

    def test_handles_mixed_whitespace(self) -> None:
        self.assertEqual(slugify("tabs\tand\nlines"), "tabs-and-lines")


if __name__ == "__main__":
    unittest.main()
