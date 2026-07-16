import unittest

from src.indexing.domain.model import Chunk


def _chunk(file_path: str, raw_frontmatter: dict) -> Chunk:
    return Chunk(
        file_path=file_path,
        chunk_index=0,
        doc_type="QAJournal",
        title="title",
        description="description",
        tags=[],
        content="content",
        parent_content="content",
        raw_frontmatter=raw_frontmatter,
        content_hash="hash",
    )


class VisibilityPolicyTests(unittest.TestCase):
    def test_qa_documents_are_private_without_frontmatter(self):
        self.assertEqual(_chunk("qa/2026-07-16/note.md", {}).to_dict()["visibility"], "private")

    def test_qa_documents_cannot_be_made_public_by_frontmatter(self):
        chunk = _chunk("qa/2026-07-16/note.md", {"visibility": "public"})
        self.assertEqual(chunk.to_dict()["visibility"], "private")

    def test_topics_remain_public_by_default(self):
        self.assertEqual(_chunk("topics/indexing.md", {}).to_dict()["visibility"], "public")

    def test_non_qa_private_frontmatter_is_preserved(self):
        chunk = _chunk("topics/private-topic.md", {"visibility": "private"})
        self.assertEqual(chunk.to_dict()["visibility"], "private")


if __name__ == "__main__":
    unittest.main()
