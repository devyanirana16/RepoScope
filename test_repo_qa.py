import unittest
from datetime import date

from repo_qa import chunk_release, search_chunks


class SearchPermissionsTest(unittest.TestCase):
    def test_revoked_repository_is_excluded_from_subsequent_searches(self):
        published_on = date(2026, 8, 14)
        public_chunk = chunk_release("payments-api", "v1", "Review", published_on)
        private_chunk = chunk_release(
            "fraud-engine", "v1", "Review threshold", published_on, confidential=True
        )
        index = {"payments-api": [public_chunk], "fraud-engine": [private_chunk]}
        repo_access = {"arjun": {"payments-api", "fraud-engine"}}

        self.assertEqual(
            search_chunks(index, repo_access, "arjun", "review threshold"),
            [private_chunk, public_chunk],
        )

        repo_access["arjun"].remove("fraud-engine")

        self.assertEqual(
            search_chunks(index, repo_access, "arjun", "review threshold"),
            [public_chunk],
        )
        with self.assertRaises(PermissionError):
            search_chunks(
                index, repo_access, "arjun", "review threshold", selected_repo="fraud-engine"
            )
