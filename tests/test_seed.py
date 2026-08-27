"""Canonical seed validation and provenance tests."""

import json
from importlib import resources

import pytest

from satori.domain.errors import InvalidSeed, UnsupportedSeedVersion
from satori.infrastructure.seeds.loader import (
    CANONICAL_V1_TRAITS,
    CANONICAL_V1_VALUES,
    JsonSeedLoader,
)


def canonical_content() -> str:
    """Read the canonical package resource as tests and production do."""

    return (
        resources.files("satori.resources.seeds")
        .joinpath("satori-v1.json")
        .read_text(encoding="utf-8")
    )


def canonical_document() -> dict[str, object]:
    """Return a mutable JSON document for negative boundary cases."""

    value: object = json.loads(canonical_content())
    assert isinstance(value, dict)
    return value


def test_canonical_seed_parses_and_matches_constitution() -> None:
    """Executable initial state contains exactly the accepted Stage 2 seed."""

    seed = JsonSeedLoader().load_canonical()

    assert seed.schema_version == 1
    assert seed.seed_id == "satori.initial.v1"
    assert seed.identity_name == "Satori"
    assert {trait.key for trait in seed.traits} == CANONICAL_V1_TRAITS
    assert {value.key for value in seed.values} == CANONICAL_V1_VALUES
    assert seed.content_hash == JsonSeedLoader().loads(canonical_content()).content_hash


def test_seed_hash_uses_canonical_validated_json() -> None:
    """Formatting changes do not alter provenance; content changes do."""

    loader = JsonSeedLoader()
    document = canonical_document()
    compact = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    original = loader.loads(canonical_content())
    reformatted = loader.loads(compact)

    assert original.content_hash == reformatted.content_hash

    personality = document["personality"]
    assert isinstance(personality, dict)
    traits = personality["traits"]
    assert isinstance(traits, list)
    first_trait = traits[0]
    assert isinstance(first_trait, dict)
    first_trait["value"] = 0.91
    assert loader.loads(json.dumps(document)).content_hash != original.content_hash


def test_unsupported_seed_schema_version_is_typed_error() -> None:
    """Version dispatch happens before interpreting an incompatible document."""

    document = canonical_document()
    document["schema_version"] = 2

    with pytest.raises(UnsupportedSeedVersion):
        JsonSeedLoader().loads(json.dumps(document))


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("schema_version",), "1"),
        (("personality", "schema_version"), "1"),
        (("personality", "traits", 0, "value"), "0.92"),
        (("identity", "name"), "   "),
    ],
)
def test_seed_schema_rejects_coercion_and_blank_identity(
    path: tuple[str | int, ...],
    invalid_value: object,
) -> None:
    """The external JSON boundary is strict instead of coercing nearby types."""

    root = canonical_document()
    document: object = root
    for segment in path[:-1]:
        if isinstance(segment, int):
            assert isinstance(document, list)
            document = document[segment]
        else:
            assert isinstance(document, dict)
            document = document[segment]
    final = path[-1]
    if isinstance(final, int):
        assert isinstance(document, list)
        document[final] = invalid_value
    else:
        assert isinstance(document, dict)
        document[final] = invalid_value

    with pytest.raises(InvalidSeed):
        JsonSeedLoader().loads(json.dumps(root))


def test_out_of_range_trait_is_rejected() -> None:
    """A seed cannot activate an invalid trait value."""

    document = canonical_document()
    personality = document["personality"]
    assert isinstance(personality, dict)
    traits = personality["traits"]
    assert isinstance(traits, list)
    trait = traits[0]
    assert isinstance(trait, dict)
    trait["value"] = 1.01

    with pytest.raises(InvalidSeed, match="seed validation failed"):
        JsonSeedLoader().loads(json.dumps(document))


def test_duplicate_trait_and_value_keys_are_rejected() -> None:
    """Logical state cannot be duplicated inside a validated seed."""

    for section, collection in (("personality", "traits"), ("values", "items")):
        document = canonical_document()
        section_document = document[section]
        assert isinstance(section_document, dict)
        items = section_document[collection]
        assert isinstance(items, list)
        items.append(items[0].copy())

        with pytest.raises(InvalidSeed, match="duplicate"):
            JsonSeedLoader().loads(json.dumps(document))


@pytest.mark.parametrize("content", ["{", "[]", "null", "{}"])
def test_malformed_seed_is_rejected(content: str) -> None:
    """Malformed shapes never reach the domain or persistence layers."""

    with pytest.raises(InvalidSeed):
        JsonSeedLoader().loads(content)
