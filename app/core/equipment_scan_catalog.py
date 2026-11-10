"""Provider-neutral canonical catalog projection and deterministic local resolver."""
import base64
import difflib
import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Text, cast, func, literal, text
from sqlalchemy.orm import selectinload

from app.core.exercise_catalog_service import catalog_version, content_hash, validate_media_publication
from app.core.settings import settings
from app.models.equipment import Equipment
from app.models.exercise import Exercise
from app.models.exercise_catalog import ExerciseMediaAsset, ExerciseMediaPublication, ExerciseMediaReview, ExercisePublication, ExerciseReview, ExerciseRevision
from app.schemas.equipment_scan import ApprovedVideo, EquipmentSearchPage, EquipmentSummary, ExerciseGuidance, ExerciseOption, ExerciseOptionsPage, PublishedInstructions, ScanCandidate, ScanConfidence, ScanVersions
from app.schemas.exercise_catalog import CatalogEntry, LoadProfile

PILOT_EQUIPMENT = {
    "crossover_cable": "cable", "hi_lo_pull_cable": "cable", "lat_pulldown_cable": "cable", "row_cable": "cable",
    "ab_crunch_machine": "selectorized", "calf_raise_machine": "selectorized", "fly_machine": "selectorized",
    "leg_curl_machine": "selectorized", "leg_extension_machine": "selectorized", "leg_press_machine_selectorized": "selectorized",
    "plate_loaded_seated_calf": "plate_loaded", "plate_loaded_t_bar": "plate_loaded",
}


class CatalogError(Exception):
    def __init__(self, code, status=404):
        self.code, self.status = code, status


def normalized(value):
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", value).casefold()))


@dataclass(frozen=True)
class EquipmentRecord:
    id: str
    name: str
    aliases: tuple
    kind: str


class CatalogRepository(Protocol):
    def versions(self) -> ScanVersions: ...
    def pilot(self) -> list[EquipmentRecord]: ...
    def options(self, equipment_id, available, limit=20, cursor=None) -> ExerciseOptionsPage: ...


def resolve(observations, catalog):
    """Only known canonical cohort identities can leave this boundary."""
    records = catalog.pilot()
    ranked = {}
    for observation in observations:
        label, kind = normalized(observation.label), observation.equipment_kind
        if kind == "unknown":
            continue
        broad_cable = kind == "cable" and label in {"cable machine", "cable station", "functional trainer", "cable crossover station"}
        for record in records:
            if record.kind != kind:
                continue
            names = [normalized(record.name), *[normalized(alias) for alias in record.aliases]]
            exact = label in names
            score = 1.0 if exact else max((difflib.SequenceMatcher(None, label, name).ratio() for name in names), default=0)
            if broad_cable:
                score = 0.75
            if score >= 0.75:
                ranked[record.id] = (max(score, ranked.get(record.id, (0,))[0]), record)
    result = []
    for _, record in sorted(ranked.values(), key=lambda pair: (-pair[0], pair[1].id))[:5]:
        options = catalog.options(record.id, [record.id], limit=3)
        result.append(ScanCandidate(equipment=EquipmentSummary(id=record.id, name=record.name), confidence=ScanConfidence(level="unknown", score=None), exercise_candidates=options.items))
    return result


class SQLCatalogRepository:
    def __init__(self, db):
        self.db = db

    def versions(self):
        equipment = self.db.execute(text("SELECT md5(coalesce(string_agg(row_to_json(e)::text, '' ORDER BY e.id),'')) FROM (SELECT id,name,aliases,is_active,updated_at FROM equipment) e")).scalar()
        exercise = self.db.execute(text("SELECT md5(coalesce(string_agg(row_to_json(e)::text, '' ORDER BY e.id),'')) FROM (SELECT id,name,is_active,source,publication_status,published_revision_id,content_version,published_media,updated_at FROM exercises WHERE owner_user_id IS NULL AND source IN ('seed','catalog') AND publication_status IN ('legacy','published')) e")).scalar()
        links = self.db.execute(text("SELECT md5(coalesce(string_agg(exercise_id || ':' || equipment_id, ',' ORDER BY exercise_id,equipment_id),'')) FROM exercise_equipment JOIN exercises ON exercises.id = exercise_equipment.exercise_id WHERE exercises.owner_user_id IS NULL AND exercises.source IN ('seed','catalog') AND exercises.publication_status IN ('legacy','published')")).scalar()
        review_revision = self.db.query(func.max(ExerciseReview.id)).scalar() or 0
        media_revision = self.db.query(func.max(ExerciseMediaReview.id)).scalar() or 0
        publication_state = hashlib.sha256((exercise + links + str(review_revision) + ":" + str(media_revision)).encode()).hexdigest()
        return ScanVersions(catalog_version=catalog_version(self.db) + "." + publication_state,
                            equipment_revision=equipment, mapping_revision="name-observations-v1", resolver_version="resolver-v1")

    def _equipment(self, identifier):
        item = self.db.query(Equipment).filter(Equipment.id == identifier, Equipment.is_active.is_(True)).first()
        if not item:
            raise CatalogError("scan_not_found")
        return item

    def pilot(self):
        rows = self.db.query(Equipment).filter(Equipment.id.in_(PILOT_EQUIPMENT), Equipment.is_active.is_(True)).order_by(Equipment.id).limit(12).all()
        result = []
        for item in rows:
            has_option = self.db.query(Exercise.id).filter(Exercise.equipment.any(Equipment.id == item.id), Exercise.is_active.is_(True), Exercise.owner_user_id.is_(None), Exercise.source.in_(["seed", "catalog"]), Exercise.publication_status.in_(["legacy", "published"])).first()
            if has_option:
                result.append(EquipmentRecord(item.id, item.name, tuple(item.aliases or []), PILOT_EQUIPMENT[item.id]))
        return result

    def _cursor(self, cursor, scope, versions, limit):
        signature = hashlib.sha256(json.dumps({"scope": scope, "versions": versions.model_dump(), "limit": limit}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not cursor:
            return 0, signature
        try:
            payload, mac = cursor.split(".")
            expected = hmac.new(settings.JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
            decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if not hmac.compare_digest(mac, expected) or decoded["scope"] != signature or type(decoded["offset"]) is not int or not 0 <= decoded["offset"] <= 10000:
                raise ValueError
            return decoded["offset"], signature
        except Exception:
            raise CatalogError("scan_result_stale", 409) from None

    def _next(self, signature, offset):
        payload = base64.urlsafe_b64encode(json.dumps({"scope": signature, "offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")
        return payload + "." + hmac.new(settings.JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def search(self, query, limit=20, cursor=None):
        versions = self.versions()
        query = normalized(query)
        offset, signature = self._cursor(cursor, {"search": query}, versions, limit)
        rows = self.db.query(Equipment).filter(Equipment.is_active.is_(True))
        if query:
            document = func.to_tsvector("simple", Equipment.name + literal(" ") + cast(Equipment.aliases, Text))
            prefix_terms = " & ".join(word + ":*" for word in query.split())
            rows = rows.filter(document.op("@@")(func.to_tsquery("simple", prefix_terms)))
        page = rows.order_by(Equipment.name, Equipment.id).offset(offset).limit(limit + 1).all()
        return EquipmentSearchPage(versions=versions, items=[EquipmentSummary(id=item.id, name=item.name) for item in page[:limit]], next_cursor=self._next(signature, offset + limit) if len(page) > limit else None)

    def guidance(self, identifier, versions=None):
        exercise = self.db.query(Exercise).filter(Exercise.id == identifier, Exercise.is_active.is_(True), Exercise.owner_user_id.is_(None), Exercise.source.in_(["seed", "catalog"]), Exercise.publication_status.in_(["legacy", "published"])).first()
        if not exercise:
            raise CatalogError("scan_not_found")
        instructions = PublishedInstructions(status="instructions_unavailable")
        video = ApprovedVideo(status="video_unavailable")
        if exercise.publication_status == "published" and exercise.published_revision_id and exercise.content_version:
            revision = self.db.get(ExerciseRevision, exercise.published_revision_id)
            publication = self.db.query(ExercisePublication).filter_by(exercise_id=exercise.id).order_by(ExercisePublication.id.desc()).first()
            if revision and revision.exercise_id == exercise.id and revision.content_hash == exercise.content_version and revision.content_hash == content_hash(revision.payload) and publication and publication.revision_id == revision.id and publication.action in {"publish", "rollback"}:
                reviews = {role: self.db.query(ExerciseReview).filter_by(revision_id=revision.id, content_hash=revision.content_hash, role=role).order_by(ExerciseReview.id.desc()).first() for role in ("trainer", "publisher")}
                trainer, publisher = reviews["trainer"], reviews["publisher"]
                if trainer and publisher and trainer.decision == publisher.decision == "approved" and trainer.reviewer != publisher.reviewer and publisher.id > trainer.id:
                    try:
                        content = CatalogEntry.model_validate(revision.payload)
                        if content.id == exercise.id:
                            instructions = PublishedInstructions(status="published", content_version=revision.content_hash, content=content.structured_content)
                    except ValueError:
                        pass
        if instructions.status == "published" and isinstance(exercise.published_media, dict):
            asset = self.db.get(ExerciseMediaAsset, exercise.published_media.get("id"))
            if asset and asset.exercise_id == exercise.id:
                publication = self.db.query(ExerciseMediaPublication).filter_by(asset_id=asset.id).order_by(ExerciseMediaPublication.id.desc()).first()
                reviews = {role: self.db.query(ExerciseMediaReview).filter_by(asset_id=asset.id, approval_hash=asset.approval_hash, role=role).order_by(ExerciseMediaReview.id.desc()).first() for role in ("trainer", "publisher")}
                trainer, publisher = reviews["trainer"], reviews["publisher"]
                try:
                    validate_media_publication(asset, asset.approval_hash)
                    if publication and publication.action in {"publish", "rollback"} and trainer and publisher and trainer.decision == publisher.decision == "approved" and trainer.reviewer != publisher.reviewer and publisher.id > trainer.id:
                        video = ApprovedVideo(status="approved", asset_id=asset.id, url=asset.published_url, mime_type=asset.mime_type, poster_url=asset.poster_url)
                except ValueError:
                    pass
        try:
            load = LoadProfile.model_validate(exercise.load_profile) if exercise.load_profile else None
        except ValueError:
            load = None
        tracking = exercise.tracking_mode if exercise.tracking_mode in {"reps", "duration", "distance"} else None
        return ExerciseGuidance(exercise_id=exercise.id, name=exercise.name, versions=versions or self.versions(), tracking_mode=tracking, load_profile=load, instructions=instructions, video=video)

    def options(self, equipment_id, available, limit=20, cursor=None):
        machine = self._equipment(equipment_id)
        available = sorted(set([equipment_id, *available]))
        valid = self.db.query(Equipment.id).filter(Equipment.id.in_(available), Equipment.is_active.is_(True)).all()
        if {item[0] for item in valid} != set(available):
            raise CatalogError("scan_invalid_request", 422)
        versions = self.versions()
        offset, signature = self._cursor(cursor, {"machine": equipment_id, "available": available}, versions, limit)
        rows = self.db.query(Exercise).options(selectinload(Exercise.equipment)).filter(Exercise.equipment.any(Equipment.id == equipment_id), Exercise.is_active.is_(True), Exercise.owner_user_id.is_(None), Exercise.source.in_(["seed", "catalog"]), Exercise.publication_status.in_(["legacy", "published"])).order_by(Exercise.name, Exercise.id).offset(offset).limit(limit + 1).all()
        result = []
        for exercise in rows[:limit]:
            required = sorted(exercise.equipment, key=lambda item: item.id)
            if any(not item.is_active for item in required):
                continue
            missing = [item for item in required if item.id not in available]
            guidance = self.guidance(exercise.id, versions)
            result.append(ExerciseOption(exercise_id=exercise.id, name=exercise.name, compatibility="needs_equipment_confirmation" if missing else "compatible", required_equipment=[EquipmentSummary(id=item.id, name=item.name) for item in required], missing_equipment=[EquipmentSummary(id=item.id, name=item.name) for item in missing], missing_equipment_ids=[item.id for item in missing], guidance_available=guidance.instructions.status == "published", approved_video_available=guidance.video.status == "approved", tracking_mode=guidance.tracking_mode, load_profile=guidance.load_profile))
        return ExerciseOptionsPage(equipment=EquipmentSummary(id=machine.id, name=machine.name), versions=versions, items=result, next_cursor=self._next(signature, offset + limit) if len(rows) > limit else None)
