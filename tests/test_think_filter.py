"""ThinkStreamFilter — streamed <think> block stripping (SEAM).

Reasoning models (MiniMax-M3) open their output with a ``<think>…</think>``
block. The wire must never carry hidden reasoning to the frontend
(2026-08-28 browser test: full English reasoning leaked into the report view),
and stream chunks may split the tags at arbitrary byte boundaries.
"""

from src.agent.llm import ThinkStreamFilter


def test_plain_text_passes_through() -> None:
    f = ThinkStreamFilter()
    assert f.feed("hello world") == "hello world"


def test_full_think_block_in_one_chunk_is_dropped() -> None:
    f = ThinkStreamFilter()
    assert f.feed("<think>secret reasoning</think>visible text") == "visible text"


def test_tags_split_across_chunks() -> None:
    f = ThinkStreamFilter()
    assert f.feed("<th") == ""
    assert f.feed("ink>hidden</thi") == ""
    assert f.feed("nk>real content") == "real content"


def test_text_before_and_after_think_survives() -> None:
    f = ThinkStreamFilter()
    assert f.feed("before<think>x</think>after") == "beforeafter"


def test_unclosed_think_suppresses_everything_after() -> None:
    f = ThinkStreamFilter()
    assert f.feed("<think>the user wants") == ""
    assert f.feed(" more secret details…") == ""


def test_partial_open_tag_is_held_back_until_decided() -> None:
    f = ThinkStreamFilter()
    assert f.feed("hello <thi") == "hello "
    assert f.feed("nk>x</think>done") == "done"


def test_partial_open_tag_that_turns_out_plain_is_emitted() -> None:
    f = ThinkStreamFilter()
    assert f.feed("a <thi") == "a "
    # "<thi" grows into literal text "<think about it" (not a real tag) —
    # the held-back characters are emitted verbatim.
    assert f.feed("nk about it") == "<think about it"


def test_two_think_blocks_both_dropped() -> None:
    f = ThinkStreamFilter()
    assert f.feed("<think>a</think>mid<think>b</think>end") == "midend"


def test_no_think_multichunk_concatenates_verbatim() -> None:
    f = ThinkStreamFilter()
    assert f.feed("## 市场") == "## 市场"
    assert f.feed("概览\n正文") == "概览\n正文"


def test_partial_close_tag_split_across_chunks() -> None:
    f = ThinkStreamFilter()
    assert f.feed("<think>reasoning</thi") == ""
    assert f.feed("nk>answer") == "answer"
