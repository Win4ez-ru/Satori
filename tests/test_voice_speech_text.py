"""Speech presentation regressions; no model, microphone or audible playback."""

# ruff: noqa: RUF001  # Russian and mathematical Unicode are intentional regression fixtures.

import pytest

from satori.desktop.voice.speech_text import render_speech_text


@pytest.mark.parametrize(
    "emoji",
    [
        "😊",
        "❤️",
        "👩🏽‍🔬",
        "👨‍👩‍👧‍👦",
        "🏳️‍🌈",
        "🇷🇺",
        "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
        "🫩",
    ],
)
def test_decorative_emoji_do_not_turn_into_spoken_names_or_join_words(emoji: str) -> None:
    assert render_speech_text(f"Да{emoji}нет.") == "Да нет."
    assert render_speech_text(emoji) == ""


def test_actual_emoji_complaint_removes_visual_formatting_only() -> None:
    canonical = "Привет 😊 **Рада тебя видеть!**"
    assert render_speech_text(canonical) == "Привет Рада тебя видеть!"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Готово😊! Продолжим❤️?", "Готово! Продолжим?"),
        ("😊 👩🏽‍🔬\n❤️", ""),
        ("Пункты 1️⃣, 2️⃣ и #️⃣; 3️⃣*️⃣4️⃣.", "Пункты 1, 2 и #; 3*4."),
        ("1\ufe0f + 2\ufe0f", "1\ufe0f + 2\ufe0f"),
        ("© 2026, ® и ™", "© 2026, ® и ™"),
        ("©️ ®️ ™️", ""),
        ("Символ ♥︎ и солнце ☀︎", "Символ ♥︎ и солнце ☀︎"),
        (
            "Шахматы ♟, масти ♠♣, солнце ☀, зонт ☂ и сердце ❤.",
            "Шахматы ♟, масти ♠♣, солнце ☀, зонт ☂ и сердце ❤.",
        ),
        (
            "1 + 2 = 3; 2*3; a_b; −2 ≤ x ≠ 5; √9=3; 20°C; №7.",
            "1 + 2 = 3; 2*3; a_b; −2 ≤ x ≠ 5; √9=3; 20°C; №7.",
        ),
        ("× ÷ ± ∞ ∑ ∫ → ↔ ➕ ➖ ➗ ✖", "× ÷ ± ∞ ∑ ∫ → ↔ ➕ ➖ ➗ ✖"),
        ("И\u0306, cafe\u0301, क्\u200dष", "И\u0306, cafe\u0301, क्\u200dष"),
    ],
)
def test_emoji_processing_preserves_words_numbers_and_meaningful_unicode(
    source: str, expected: str
) -> None:
    assert render_speech_text(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "# План\n- **Проверь** текст.\n> Затем *отправь*.",
            "План\nПроверь текст.\nЗатем отправь.",
        ),
        (
            "**Важно:** __не спеши__; ~~старое~~ *новое* _слово_.",
            "Важно: не спеши; старое новое слово.",
        ),
        ("Открой [документацию](https://example.com/a_(b)) сейчас.", "Открой документацию сейчас."),
        ("[**Справка** 😊](../help.md) и ![описание](image.png)", "Справка и описание"),
        ("`x = 2*3; a_b <= 7` и **готово**.", "x = 2*3; a_b <= 7 и готово."),
        ("``a ` b``", "a ` b"),
        ("**`**` внутри**", "** внутри"),
        ("***важно*** и *не закрыто **готово**", "важно и *не закрыто готово"),
        ("Код:\n```python\nx = 2*3\ny = a_b\n```\nГотово 😊.", "Код:\nx = 2*3\ny = a_b\nГотово."),
        ("До\n~~~~python\nprint('**literal**')\n~~~~\nПосле", "До\nprint('**literal**')\nПосле"),
        (r"\*буквально\* и a_b, 2**3, 2 * 3 * 4", "*буквально* и a_b, 2**3, 2 * 3 * 4"),
        ("**не закрыто и [ссылка](без конца", "**не закрыто и [ссылка](без конца"),
    ],
)
def test_balanced_markdown_is_unwrapped_without_rewording_or_erasing_code(
    source: str, expected: str
) -> None:
    assert render_speech_text(source) == expected


def test_emoji_in_literal_code_is_content_rather_than_prose_decoration() -> None:
    assert render_speech_text("`icon = '😊'` 😊") == "icon = '😊'"


def test_plain_conversational_text_keeps_its_words_and_punctuation() -> None:
    source = "Не уверена — давай проверим.\n\nА потом решим, что менять."
    assert render_speech_text(source) == source


def test_maximum_size_malformed_markup_does_not_recurse_without_a_bound() -> None:
    source = "[" * 4000 + "x" * 4000
    assert render_speech_text(source) == source
    source = "*a " * 2666 + "xx"
    assert render_speech_text(source) == source
