import asyncio
from io import BytesIO

import pytest
from PIL import Image

from app.core.equipment_scan_catalog import EquipmentRecord, resolve
from app.core.equipment_scan_provider import EquipmentObservation, FixtureRecognitionAdapter, RecognitionResult, ScanProviderError, normalize_image
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
    assert len(broad) == 2 and all(item.confidence.level == "unknown" and item.confidence.score is None for item in broad)


def test_subject_gate_cannot_return_machines_for_collage_or_unclear_image():
    for subject in ["multiple_subjects", "not_equipment", "image_unclear", "unsupported_equipment", "uncertain"]:
        with pytest.raises(ValueError):
            RecognitionResult(subject=subject, observations=[EquipmentObservation(label="machine", equipment_kind="unknown")])
    adapter = FixtureRecognitionAdapter({"subject": "single_equipment", "observations": [{"label": "Lat Pulldown", "equipment_kind": "cable"}]}, blocked=True)
    with pytest.raises(ScanProviderError):
        asyncio.run(adapter.moderate(b"normalized", 1))
    assert adapter.calls == ["moderation"]
