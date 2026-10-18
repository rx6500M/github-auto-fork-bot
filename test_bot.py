import unittest
from datetime import timezone

import bot


class BotHelpersTest(unittest.TestCase):
    def test_owner_validation(self):
        self.assertEqual(bot.normalize_owner("Clash-Projects"), "Clash-Projects")
        self.assertEqual(bot.normalize_owner("@octocat"), "octocat")
        self.assertIsNone(bot.normalize_owner("https://github.com/octocat"))
        self.assertIsNone(bot.normalize_owner("bad name"))

    def test_parse_utc_z(self):
        parsed = bot.parse_time("2026-10-18T00:00:00Z")
        self.assertEqual(parsed.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
