"""Comprehensive tests for the text chunking engine."""

import pytest

from acheron.core.chunking import chunk_text
from acheron.core.errors import ChunkingError


class TestChunkTextBasic:
    """Basic chunking behavior."""

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


class TestChunkTextSplitting:
    """Splitting behavior for long text."""

    def test_long_sentence_splits_on_comma(self) -> None:
        text = "This is a long sentence, with a comma in the middle, that should be split into parts."
        result = chunk_text(text, "ch1", max_length=50)
        for chunk in result:
            assert len(chunk.text) <= 50
            assert chunk.text.strip()

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
        combined = " ".join(c.text for c in result)
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

    def test_text_at_exact_max_length(self) -> None:
        text = "A" * 250
        result = chunk_text(text, "ch1", max_length=250)
        assert len(result) == 1
        assert result[0].text == "A" * 250

    def test_text_one_over_max_length(self) -> None:
        text = "A" * 251
        result = chunk_text(text, "ch1", max_length=250)
        assert len(result) == 2

    def test_preserves_content(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        result = chunk_text(text, "ch1")
        combined = " ".join(c.text for c in result)
        for word in text.split():
            assert word in combined


class TestChunkTextWhitespace:
    """Whitespace normalization."""

    def test_normalizes_internal_whitespace(self) -> None:
        text = "Hello   world.\n\n  Second   sentence."
        result = chunk_text(text, "ch1")
        for chunk in result:
            assert "  " not in chunk.text
            assert "\n" not in chunk.text

    def test_leading_trailing_whitespace(self) -> None:
        text = "   Hello world.   "
        result = chunk_text(text, "ch1")
        assert len(result) == 1
        assert result[0].text == "Hello world."

    def test_tabs_and_newlines(self) -> None:
        text = "Hello\t\tworld.\n\n\nSecond\tsentence."
        result = chunk_text(text, "ch1")
        assert len(result) == 2


class TestChunkTextUnicode:
    """Unicode and international text."""

    def test_spanish_text(self) -> None:
        text = "Hola mundo. Esta es una segunda oración."
        result = chunk_text(text, "ch1")
        assert len(result) == 2
        assert result[0].text == "Hola mundo."

    def test_chinese_text(self) -> None:
        text = "这是一段中文文本。这是第二句话。"
        result = chunk_text(text, "ch1")
        assert len(result) >= 1

    def test_japanese_text(self) -> None:
        text = "これは日本語のテキストです。二番目の文です。"
        result = chunk_text(text, "ch1")
        assert len(result) >= 1

    def test_emoji_text(self) -> None:
        text = "Hello 🌍 world. Second sentence 🎉."
        result = chunk_text(text, "ch1")
        assert len(result) == 2

    def test_mixed_scripts(self) -> None:
        text = "Hello мир. こんにちは世界."
        result = chunk_text(text, "ch1")
        assert len(result) == 2

    def test_rtl_arabic(self) -> None:
        text = "مرحبا بالعالم. هذه جملة ثانية."
        result = chunk_text(text, "ch1")
        assert len(result) >= 1


class TestChunkTextNumbers:
    """Numeric and mixed content."""

    def test_numbers_only(self) -> None:
        text = "12345. 67890."
        result = chunk_text(text, "ch1")
        assert len(result) == 2

    def test_mixed_numbers_text(self) -> None:
        text = "Chapter 1 begins here. Page 42 has the answer."
        result = chunk_text(text, "ch1")
        assert len(result) == 2

    def test_decimal_numbers(self) -> None:
        text = "The value is 3.14159. The ratio is 2.71828."
        result = chunk_text(text, "ch1")
        assert len(result) >= 1


class TestChunkTextPunctuation:
    """Punctuation edge cases."""

    def test_punctuation_only(self) -> None:
        text = "!!! ??? ..."
        result = chunk_text(text, "ch1")
        assert len(result) >= 1
        for chunk in result:
            assert chunk.text.strip()

    def test_consecutive_punctuation(self) -> None:
        text = "Really?! Yes!! No..."
        result = chunk_text(text, "ch1")
        assert len(result) >= 1

    def test_text_starting_with_punctuation(self) -> None:
        text = "(Hello world.) (Second sentence.)"
        result = chunk_text(text, "ch1")
        assert len(result) >= 1

    def test_text_ending_with_punctuation(self) -> None:
        text = "Hello world! Second sentence?"
        result = chunk_text(text, "ch1")
        assert len(result) == 2

    def test_quotation_marks(self) -> None:
        text = '"Hello," she said. "Goodbye," he replied.'
        result = chunk_text(text, "ch1")
        assert len(result) == 2


class TestChunkTextLongContent:
    """Realistic long content."""

    def test_paragraph_length_text(self) -> None:
        text = (
            "The quick brown fox jumps over the lazy dog. "
            "This is a longer paragraph with multiple sentences that should be "
            "split into reasonable chunks for text-to-speech processing. "
            "Each chunk should be under the specified character limit. "
            "The chunking engine handles this automatically."
        )
        result = chunk_text(text, "ch1", max_length=100)
        for chunk in result:
            assert len(chunk.text) <= 100
            assert chunk.text.strip()

    def test_chapter_length_text(self) -> None:
        sentences = [f"This is sentence number {i} in the chapter." for i in range(100)]
        text = " ".join(sentences)
        result = chunk_text(text, "ch1", max_length=250)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk.text) <= 250
            assert chunk.text.strip()

    def test_realistic_epub_paragraph(self) -> None:
        text = (
            "When Gregor Samsa woke one morning from troubled dreams, "
            "he found himself transformed in his bed into a monstrous insect. "
            "He was lying on his hard, as it were armour-plated, back "
            "and when he lifted his head a little he could see his dome-like "
            "brown belly divided into stiff arched segments."
        )
        result = chunk_text(text, "ch1", max_length=250)
        assert len(result) >= 1
        for chunk in result:
            assert len(chunk.text) <= 250


class TestChunkTextEdgeCases:
    """Boundary and error conditions."""

    def test_single_character(self) -> None:
        result = chunk_text("A", "ch1")
        assert len(result) == 1
        assert result[0].text == "A"

    def test_two_characters(self) -> None:
        result = chunk_text("AB", "ch1")
        assert len(result) == 1
        assert result[0].text == "AB"

    def test_single_word(self) -> None:
        result = chunk_text("Hello", "ch1")
        assert len(result) == 1
        assert result[0].text == "Hello"

    def test_single_long_word_no_spaces(self) -> None:
        text = "A" * 500
        result = chunk_text(text, "ch1", max_length=100)
        assert len(result) == 5
        combined = "".join(c.text for c in result)
        assert combined == text

    def test_max_length_one(self) -> None:
        text = "ABC"
        result = chunk_text(text, "ch1", max_length=1)
        assert len(result) == 3
        for chunk in result:
            assert len(chunk.text) <= 1


class TestChunkTextValidation:
    """Output validation and error handling."""

    def test_invalid_max_length_zero(self) -> None:
        with pytest.raises(ChunkingError, match="max_length must be >= 1"):
            chunk_text("Hello", "ch1", max_length=0)

    def test_invalid_max_length_negative(self) -> None:
        with pytest.raises(ChunkingError, match="max_length must be >= 1"):
            chunk_text("Hello", "ch1", max_length=-1)

    def test_no_empty_chunks_in_output(self) -> None:
        text = "First. Second. Third."
        result = chunk_text(text, "ch1")
        for chunk in result:
            assert chunk.text.strip()

    def test_content_integrity(self) -> None:
        text = "The quick brown fox. Jumps over the lazy dog."
        result = chunk_text(text, "ch1")
        rejoined = " ".join(c.text for c in result)
        assert rejoined == text

    def test_content_integrity_with_splitting(self) -> None:
        text = "A" * 100 + " " + "B" * 100 + " " + "C" * 100
        result = chunk_text(text, "ch1", max_length=150)
        rejoined = " ".join(c.text for c in result)
        assert rejoined == text

    def test_content_words_preserved_through_punctuation_split(self) -> None:
        text = "This is a long sentence, with a comma in the middle, that should be split."
        result = chunk_text(text, "ch1", max_length=50)
        all_text = " ".join(c.text for c in result)
        assert "sentence," in all_text
        assert "middle," in all_text
        assert "split." in all_text
