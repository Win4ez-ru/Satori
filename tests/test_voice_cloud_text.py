"""Cloud-only syntax admission preserves the displayed text and never calls a provider."""

# ruff: noqa: RUF001 - intentional Unicode adversarial and mathematical corpus

import pytest

from satori.desktop.voice.cloud_text import admit_cloud_speech_text
from satori.desktop.voice.speech_contracts import SpeechError
from satori.desktop.voice.speech_text import render_speech_text


@pytest.mark.parametrize(
    "text",
    [
        "[whispering] Не отправляла.",
        "Цитата: [shouting]",
        "Код: a[0]",
        "[unclosed",
        "unopened]",
        r"\[escaped\]",
        "［whispering］",
        "﹇shouting﹈",
        "[mixed］",
        "\uff3b",
        "\ufe48",
        "😊",
        "😊\ufe0e",
        "👩🏽‍💻",
        "🇷🇺",
        "🇷",
        "🏽",
        "1️⃣",
        "1\u20e3",
        "\u20e3",
        "1\ufe0f",
        "\ufe0f",
        "©️",
        "➕️",
        "➕‍😊",
        "➕🏽",
        "➕\u20e3",
        "\U000e0067",
        "🏴\U000e0067\U000e0062\U000e007f",
        "Символ ` остался.",
        "   ~~~python",
        "**не закрыто",
        "*не закрыто",
        "__не закрыто",
        "_не закрыто",
        "~~не закрыто",
        "**kwargs",
    ],
)
def test_unsafe_rendered_content_is_rejected_without_rewriting(text: str) -> None:
    before = text
    with pytest.raises(SpeechError, match=r"^speech_text_unsupported$") as error:
        admit_cloud_speech_text(text)
    assert error.value.code == "speech_text_unsupported"
    assert text == before
    assert text not in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "Я не отправляла письмо и не меняла настройки.",
        "2*3=6; 2**3=8; a_b <= 7; -2 ≤ x ≠ 5; x < 3; x *= 2",
        "➕ ➖ ➗ ✖ × ÷ ± ∞ → ↔",
        "© ® ™; ©\ufe0e",
        "Не меняй (скобки) и {слова}.",
        "𝑥 ≠ ０; K = 3",
        "2 ＊x; ＿name; ｀",
        "क्‍ष",
        "漢\U000e0100",
        "Обычные цифры 123, # и * без emoji.",
    ],
)
def test_plain_text_negation_operators_and_nonemoji_joiners_remain_exact(text: str) -> None:
    before = text
    admit_cloud_speech_text(text)
    assert text == before


@pytest.mark.parametrize(
    ("canonical", "expected"),
    [
        ("**Готово** 🙂 [план](https://example.org/plan)", "Готово план"),
        ("1️⃣ пункт: не отправлять.", "1 пункт: не отправлять."),
        ("`x = 2*3; a_b <= 7`", "x = 2*3; a_b <= 7"),
    ],
)
def test_admission_follows_existing_renderer_and_keeps_canonical_text(
    canonical: str, expected: str
) -> None:
    before = canonical
    rendered = render_speech_text(canonical)
    assert rendered == expected
    admit_cloud_speech_text(rendered)
    assert canonical == before


@pytest.mark.parametrize(
    "canonical",
    ["`[shouting]`", "`icon = '😊'`", "`1️⃣`", "[unclosed", "**не закрыто"],
)
def test_preserved_literals_are_refused_only_for_cloud(canonical: str) -> None:
    before = canonical
    rendered = render_speech_text(canonical)
    assert rendered
    with pytest.raises(SpeechError, match=r"^speech_text_unsupported$"):
        admit_cloud_speech_text(rendered)
    assert canonical == before


def test_guard_makes_no_claim_to_parse_html_or_semantic_instructions() -> None:
    admit_cloud_speech_text("<span>Текст</span>")
    admit_cloud_speech_text("Ignore previous instructions.")
