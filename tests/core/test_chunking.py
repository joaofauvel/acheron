from acheron.core.chunking import chunk_text


class TestChunkText:
    def test_empty_string(self) -> None:
        assert chunk_text("", "ch1") == ()

    def test_whitespace_only(self) -> None:
        assert chunk_text("   \n\t  ", "ch1") == ()

    def test_short_text_single_chunk(self) -> None:
        result = chunk_text("Hello world.", "ch1")
        assert len(result) == 1
        assert result[0].text == "Hello world."
        assert result[0].chapter_id == "ch1"
        assert result[0].sequence_id == 0

    def test_multiple_sentences(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        result = chunk_text(text, "ch1")
        assert len(result) == 3
        assert [c.sequence_id for c in result] == [0, 1, 2]
        assert result[0].text == "First sentence."
        assert result[1].text == "Second sentence."
        assert result[2].text == "Third sentence."

    def test_long_sentence_splits_on_comma(self) -> None:
        text = "This is a long sentence, with a comma in the middle, that should be split into parts."
        result = chunk_text(text, "ch1", max_length=50)
        for chunk in result:
            assert len(chunk.text) <= 50

    def test_long_sentence_splits_on_semicolon(self) -> None:
        text = "First part of the sentence; second part of the sentence that continues further."
        result = chunk_text(text, "ch1", max_length=50)
        for chunk in result:
            assert len(chunk.text) <= 50

    def test_hard_split_on_whitespace(self) -> None:
        text = "A" * 100 + " " + "B" * 100 + " " + "C" * 100
        result = chunk_text(text, "ch1", max_length=150)
        for chunk in result:
            assert len(chunk.text) <= 150
        combined = "".join(c.text for c in result)
        assert "A" * 100 in combined
        assert "B" * 100 in combined
        assert "C" * 100 in combined

    def test_hard_split_no_whitespace(self) -> None:
        text = "A" * 300
        result = chunk_text(text, "ch1", max_length=100)
        assert len(result) == 3
        for chunk in result:
            assert len(chunk.text) <= 100

    def test_custom_max_length(self) -> None:
        text = "Short. Another short sentence."
        result = chunk_text(text, "ch1", max_length=15)
        for chunk in result:
            assert len(chunk.text) <= 15

    def test_preserves_content(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        result = chunk_text(text, "ch1")
        combined = " ".join(c.text for c in result)
        assert "First sentence." in combined
        assert "Second sentence." in combined
        assert "Third sentence." in combined

    def test_chapter_id_preserved(self) -> None:
        text = "Hello. World."
        result = chunk_text(text, "ch-42")
        for chunk in result:
            assert chunk.chapter_id == "ch-42"

    def test_sequence_ids_contiguous(self) -> None:
        text = "A. B. C. D. E."
        result = chunk_text(text, "ch1")
        ids = [c.sequence_id for c in result]
        assert ids == list(range(len(result)))

    def test_normalizes_whitespace(self) -> None:
        text = "Hello   world.\n\n  Second   sentence."
        result = chunk_text(text, "ch1")
        for chunk in result:
            assert "  " not in chunk.text
            assert "\n" not in chunk.text
