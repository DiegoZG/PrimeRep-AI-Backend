import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.equipment_scan_catalog import EquipmentRecord, resolve
from app.core import equipment_scan_provider as provider
from app.core.equipment_scan_provider import EquipmentObservation, FixtureRecognitionAdapter, OpenAIRecognitionAdapter, RecognitionResult, ScanProviderError, normalize_image
from app.schemas.equipment_scan import EquipmentSummary, ExerciseOptionsPage, ScanVersions


def photo(size=(1800, 900), kind="PNG"):
    target = BytesIO()
    with Image.new("RGB", size, "blue") as image:
        image.save(target, kind)
    return target.getvalue()


def test_orientation_safe_normalization_is_bounded_not_square_and_strips_metadata():
    result = normalize_image(photo(), "image/png")
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG" and image.size == (1024, 512)
        assert not image.getexif() and "icc_profile" not in image.info
    result = normalize_image(photo((100, 70)), "image/png")
    with Image.open(BytesIO(result)) as image:
        assert image.size == (100, 70)


@pytest.mark.parametrize("raw,mime,code", [(b"not-image", "image/png", "scan_image_invalid"), (photo(), "image/jpeg", "scan_media_type_unsupported"), (photo(), "application/pdf", "scan_media_type_unsupported"), (b"x" * (5 * 1024 * 1024 + 1), "image/png", "scan_image_too_large")])
def test_invalid_uploads_stop_at_decode_boundary(raw, mime, code):
    with pytest.raises(ScanProviderError) as error:
        normalize_image(raw, mime)
    assert error.value.code == code


def test_multiframe_png_is_rejected():
    target = BytesIO()
    with Image.new("RGB", (10, 10), "red") as first, Image.new("RGB", (10, 10), "blue") as second:
        first.save(target, "PNG", save_all=True, append_images=[second])
    with pytest.raises(ScanProviderError):
        normalize_image(target.getvalue(), "image/png")


class Catalog:
    def pilot(self):
        return [EquipmentRecord("leg_press_machine_selectorized", "Leg Press Machine", (), "selectorized"), EquipmentRecord("lat_pulldown_cable", "Lat Pulldown Cable", ("Lat Pulldown",), "cable"), EquipmentRecord("hi_lo_pull_cable", "Hi Lo Pull Cable", (), "cable")]
    def options(self, identifier, available, limit=3):
        return ExerciseOptionsPage(equipment=EquipmentSummary(id=identifier, name=identifier), versions=ScanVersions(catalog_version="0", equipment_revision="0", mapping_revision="0", resolver_version="1"), items=[])


def test_resolver_keeps_lookalikes_unmapped_and_broad_cable_ambiguous():
    wrong_load = EquipmentObservation(label="Plate-loaded leg press", equipment_kind="plate_loaded")
    assert resolve([wrong_load], Catalog()) == []
    unknown = EquipmentObservation(label="Leg Press Machine", equipment_kind="unknown")
    assert resolve([unknown], Catalog()) == []
    actual = resolve([EquipmentObservation(label="Lat Pulldown", equipment_kind="cable")], Catalog())
    assert [item.equipment.id for item in actual] == ["lat_pulldown_cable"]
    broad = resolve([EquipmentObservation(label="Cable machine", equipment_kind="cable")], Catalog())
    assert broad == []


def test_subject_gate_cannot_return_machines_for_collage_or_unclear_image():
    for subject in ["multiple_subjects", "not_equipment", "image_unclear", "unsupported_equipment", "uncertain"]:
        with pytest.raises(ValueError):
            RecognitionResult(image_type="photo", subject=subject, observations=[EquipmentObservation(label="machine", equipment_kind="unknown")])
    adapter = FixtureRecognitionAdapter({"image_type": "photo", "subject": "single_equipment", "observations": [{"label": "Lat Pulldown", "equipment_kind": "cable"}]}, blocked=True)
    with pytest.raises(ScanProviderError):
        asyncio.run(adapter.moderate(b"normalized", 1))
    assert adapter.calls == ["moderation"]


@pytest.mark.parametrize("label,kind", [("Lat Pullup Cable", "cable"), ("Lat Pulldown Cables", "cable"), ("Leg Press Machines", "selectorized"), ("Cable station", "cable"), ("Functional trainer", "cable")])
def test_similar_words_and_generic_stations_do_not_authorize_a_machine(label, kind):
    assert resolve([EquipmentObservation(label=label, equipment_kind=kind)], Catalog()) == []


def test_exact_owned_aliases_normalize_case_and_punctuation_without_claiming_confidence():
    result = resolve([EquipmentObservation(label="  LAT-PULLDOWN  ", equipment_kind="cable")], Catalog())
    assert [item.equipment.id for item in result] == ["lat_pulldown_cable"]
    assert result[0].confidence.level == "unknown" and result[0].confidence.score is None


def test_shared_alias_cannot_be_disambiguated_by_the_provider_guessing_a_kind():
    class Collision(Catalog):
        def pilot(self):
            return [EquipmentRecord("first", "First", ("Shared station",), "cable"), EquipmentRecord("second", "Second", ("Shared station",), "selectorized")]
    assert resolve([EquipmentObservation(label="Shared station", equipment_kind="cable")], Collision()) == []


def test_conflicting_named_machines_are_uncertain_but_duplicate_aliases_are_one_candidate():
    two = [EquipmentObservation(label="Lat Pulldown", equipment_kind="cable"), EquipmentObservation(label="Hi Lo Pull Cable", equipment_kind="cable")]
    assert resolve(two, Catalog()) == []
    one = [EquipmentObservation(label="Lat Pulldown", equipment_kind="cable"), EquipmentObservation(label="Lat Pulldown Cable", equipment_kind="cable")]
    assert [item.equipment.id for item in resolve(one, Catalog())] == ["lat_pulldown_cable"]


@pytest.mark.parametrize("label,kind", [("Pull-up bar", "unknown"), ("Unlisted pull station", "cable"), ("Cable station", "cable"), ("Leg Press Machine", "cable"), ("Shared station", "cable")])
@pytest.mark.parametrize("reverse", [False, True])
def test_one_exact_match_cannot_hide_an_unclear_or_ambiguous_alternative(label, kind, reverse):
    class Mixed(Catalog):
        def pilot(self):
            return super().pilot() + [EquipmentRecord("first", "First", ("Shared station",), "cable"), EquipmentRecord("second", "Second", ("Shared station",), "cable")]
    observations = [EquipmentObservation(label="Lat Pulldown", equipment_kind="cable"), EquipmentObservation(label=label, equipment_kind=kind)]
    assert resolve(list(reversed(observations)) if reverse else observations, Mixed()) == []


def test_missing_image_classification_is_not_assumed_to_be_a_real_photo():
    with pytest.raises(ValueError) as error:
        RecognitionResult(subject="single_equipment", observations=[EquipmentObservation(label="Lat Pulldown", equipment_kind="cable")])
    assert error.value.errors()[0]["loc"] == ("image_type",)
    schema = RecognitionResult.model_json_schema()
    assert "image_type" in schema["required"] and "default" not in schema["properties"]["image_type"]


def test_recognition_prompt_is_bounded_to_real_visible_machine_evidence(monkeypatch):
    captured = []
    async def parse(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(status="completed", output_parsed=RecognitionResult(image_type="photo", subject="uncertain", observations=[]), usage=None)
    class Client:
        async def __aenter__(self):
            return SimpleNamespace(responses=SimpleNamespace(parse=parse))
        async def __aexit__(self, *args):
            return None
    monkeypatch.setattr(provider, "_client", lambda timeout: Client())
    result, _ = asyncio.run(OpenAIRecognitionAdapter().recognize(b"fixture-only", Catalog().pilot(), 1))
    assert result.subject == "uncertain" and len(captured) == 1
    request = captured[0]
    assert request["store"] is False and request["tools"] == [] and request["max_output_tokens"] == 768
    assert "Drawings, renders, cartoons" in request["instructions"]
    assert "fixed pull-up bar without a cable" in request["instructions"]
    assert "Dual-purpose" in request["instructions"] and "Do not force a closest catalog match" in request["instructions"]


def test_prompt_revision_changes_the_image_receipt_settings_namespace(monkeypatch):
    before = provider.settings_version()
    monkeypatch.setattr(provider, "RECOGNITION_VERSION", "future-reviewed-recognition")
    assert provider.settings_version() != before
