"""Repetition guards.

A model that starts looping under long context will fill its entire output
window with one paragraph. Left alone that lands in the log, gets read back
into the next turn's context, and teaches the model to keep doing it - so the
guard has to hold on the way out AND on the way in.
"""

from __future__ import annotations

from app.modules.narrative.turn_loop import (
    MAX_NARRATION_CHARS,
    MAX_NARRATION_TOKENS,
    strip_repetition,
)


def test_paragraph_loop_is_cut():
    block = "A beat.\n\nAnother beat.\n\nA third."
    spam = "\n\n".join([block] * 80)
    assert len(strip_repetition(spam).split("\n\n")) <= 4


def test_sentence_loop_with_no_paragraph_breaks_is_cut():
    """A loop does not always break on paragraphs."""
    spam = "The door creaks open. " * 60
    assert len(strip_repetition(spam)) < 100


def test_unstructured_wall_is_capped():
    """Neither heuristic fires on text with no structure at all."""
    assert len(strip_repetition("x" * 50_000)) <= MAX_NARRATION_CHARS


def test_normal_prose_is_untouched():
    good = (
        "The device clicks to life in her hands.\n\n"
        "Around the square, people are beginning to notice.\n\n"
        "The clock is ticking."
    )
    assert strip_repetition(good) == good


def test_repeated_words_within_a_sentence_are_fine():
    """Deduping must not mangle legitimate repetition for effect."""
    line = "It was cold. Very cold. Colder than she had words for."
    assert strip_repetition(line) == line


def test_empty_input_survives():
    assert strip_repetition("") == ""


def test_narration_budget_is_bounded():
    """The output cap is the first line of defence - the guards only run on
    what the model already produced and billed."""
    assert MAX_NARRATION_TOKENS <= 1200
