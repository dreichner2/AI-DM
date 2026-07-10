from __future__ import annotations

from aidm_server.text_sanitization import ReasoningBlockFilter, normalize_tts_text, strip_reasoning_blocks


def test_strip_reasoning_blocks_removes_closed_and_unclosed_tags():
    text = 'Visible. <thought>hidden reasoning</thought> Still visible. <think>unfinished'

    assert strip_reasoning_blocks(text) == 'Visible.  Still visible. '


def test_reasoning_block_filter_handles_tags_split_across_chunks():
    filter_ = ReasoningBlockFilter()

    chunks = [
        filter_.filter('The door '),
        filter_.filter('<thought>secret'),
        filter_.filter(' still secret</thought>opens. <thi'),
        filter_.filter('nk>more secret</think>'),
        filter_.finish(),
    ]

    assert ''.join(chunks) == 'The door opens. '


def test_normalize_tts_text_removes_reasoning_and_markdown():
    text = '# Scene\n![map](https://example.test/map.png)\nVisible **words** and [label](https://example.test). <think>hidden</think>'

    assert normalize_tts_text(text) == 'Scene Visible words and label.'


def test_normalize_tts_text_handles_long_unclosed_markdown_candidates():
    for fragment in ('[x', '!['):
        text = fragment * 20_000

        assert normalize_tts_text(text) == text
