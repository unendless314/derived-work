"""
Pointer validation tests (IMPLEMENTATION_PLAN.md §4, "Pointer and language
authority"): parity with the publish writer and the site reader
(DATA_DEPENDENCIES.md §4).
"""

import json
import pathlib

import pytest

from modules.api.src.export_pointer import (
    PointerError,
    is_valid_generation_id,
    is_valid_iso_timestamp,
    read_pointer,
    resolve_generation_root,
)
from modules.api.tests import support


def test_valid_pointer_resolves(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    pointer = read_pointer(export_dir)
    assert pointer.generation == support.GENERATION_CURRENT
    assert pointer.languages == ("zh", "en")
    assert pointer.export_completed_at == "2026-08-05T15:23:51Z"
    assert pointer.last_successful_run_at == "2026-08-05T15:23:51Z"
    root = resolve_generation_root(export_dir, pointer)
    assert root == export_dir / "generations" / support.GENERATION_CURRENT
    assert root.is_dir()


def test_missing_pointer_is_bootstrap_state(tmp_path):
    with pytest.raises(PointerError):
        read_pointer(tmp_path)


def test_unparseable_pointer_rejected(tmp_path):
    (tmp_path / "current.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PointerError):
        read_pointer(tmp_path)


def test_pointer_top_level_must_be_object(tmp_path):
    support.write_json(tmp_path / "current.json", ["not", "an", "object"])
    with pytest.raises(PointerError):
        read_pointer(tmp_path)


@pytest.mark.parametrize(
    "generation",
    [
        "../../etc",
        "..\\..\\windows",
        "2026-08-05T15:23:51Z",  # colons are not the Windows-safe id form
        "2026-08-05 15-23-51Z",
        "not-a-generation",
        12345,
        None,
    ],
)
def test_invalid_generation_id_rejected_before_any_path_join(tmp_path, generation):
    support.write_pointer(tmp_path, generation=generation)
    with pytest.raises(PointerError):
        read_pointer(tmp_path)
    # No directory resolution may have happened for a rejected id.
    assert not (tmp_path / "generations").exists()


def test_collision_suffix_generation_id_accepted(tmp_path):
    generation = support.GENERATION_CURRENT + "-r2"
    support.write_generation(
        tmp_path,
        generation,
        {"zh": [support.make_item_record("alpha-tie-break", "2026-08-04T09:00:00Z")]},
    )
    support.write_pointer(tmp_path, generation=generation, languages=("zh",))
    pointer = read_pointer(tmp_path)
    assert pointer.generation == generation
    assert resolve_generation_root(tmp_path, pointer).is_dir()


def test_calendar_impossible_timestamp_rejected(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, last_successful_run_at="2026-02-30T00:00:00Z")
    with pytest.raises(PointerError):
        read_pointer(export_dir)


@pytest.mark.parametrize("value", ["2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z",
                                   "2026-08-05 15:23:51Z", "2026-08-05T15:23:51+00:00",
                                   "", None, 0])
def test_iso_timestamp_validity(value):
    assert not is_valid_iso_timestamp(value)


def test_iso_timestamp_valid():
    assert is_valid_iso_timestamp("2026-08-05T15:23:51Z")


@pytest.mark.parametrize(
    "languages",
    [[], "zh", ["zh", 3], None],
)
def test_invalid_languages_rejected(tmp_path, languages):
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, languages=languages)
    with pytest.raises(PointerError):
        read_pointer(export_dir)


@pytest.mark.parametrize(
    "languages",
    [["../.."], ["..\\.."], ["zh", "../en"], ["EN"], ["zh", "zh;rm"]],
)
def test_unsafe_language_code_rejected_before_any_path_join(tmp_path, languages):
    # Language codes double as generation path components, so entries
    # outside the safe lowercase form are an invalid pointer (503), never a
    # path traversal vector.
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, languages=languages)
    with pytest.raises(PointerError, match="safe path components"):
        read_pointer(export_dir)


def test_hyphenated_lowercase_language_code_accepted(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, languages=("zh-tw", "en"))
    pointer = read_pointer(export_dir)
    assert pointer.languages == ("zh-tw", "en")


@pytest.mark.parametrize(
    "fingerprint",
    ["sha256:abc", "sha256-exportstate-v1:xyz", "", None],
)
def test_invalid_fingerprint_rejected(tmp_path, fingerprint):
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, content_fingerprint=fingerprint)
    with pytest.raises(PointerError):
        read_pointer(export_dir)


def test_generation_directory_missing_raises_file_not_found(tmp_path):
    # Field validation passes; resolution reports the vanished directory so
    # the read flow enters the single-retry scope of API_CONTRACT.md §5.
    support.write_pointer(tmp_path, generation=support.GENERATION_CURRENT)
    pointer = read_pointer(tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_generation_root(tmp_path, pointer)


def test_is_valid_generation_id():
    assert is_valid_generation_id("2026-08-05T15-23-51Z")
    assert is_valid_generation_id("2026-08-05T15-23-51Z-r12")
    assert not is_valid_generation_id("2026-08-05T15-23-51Z-r")
    assert not is_valid_generation_id("2026-08-05T15-23-51Z-rx")
