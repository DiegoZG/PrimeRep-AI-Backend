"""
Legacy exercise draft generator. Direct publishing is retired.

Use exercise_catalog.py for validated imports and actual trainer/publication
reviews tied to immutable hashes. Existing content has legacy provenance.

Usage
-----
    # 1. (Optional) Generate a first draft with Claude Sonnet. Requires
    #    ANTHROPIC_API_KEY. Writes scripts/data/exercise_content_draft.json.
    python scripts/seed_exercise_content.py generate

    # 2. Adapt drafts to the CatalogEntry schema and use exercise_catalog.py.

Renaming files is not an approval. The former upsert command always fails.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

DATA_DIR = Path(__file__).resolve().parent / "data"
DRAFT_PATH = DATA_DIR / "exercise_content_draft.json"
REVIEWED_PATH = DATA_DIR / "exercise_content.json"

CONTENT_FIELDS = ["how_to", "why_it_works", "common_mistakes", "beginner_notes"]

DEFAULT_SEED_MODEL = "claude-sonnet-4-5-20250929"


def _seed_exercise_ids() -> list[dict]:
    """(id, name) pairs mirrored from the b7c8d9e0f1a2 seed migration, used so
    `generate` can run without a live DB connection.

    Loaded by file path (rather than `import alembic.versions...`) because the
    local `alembic/` script directory shares its top-level name with the
    installed `alembic` pip package, which would otherwise shadow it.
    """
    migration_path = (
        ROOT_DIR / "alembic" / "versions" / "b7c8d9e0f1a2_add_exercises_tables.py"
    )
    spec = importlib.util.spec_from_file_location("_seed_migration", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return [{"id": e["id"], "name": e["name"]} for e in module.SEED_EXERCISES]


def cmd_generate(_args: argparse.Namespace) -> None:
    """Draft content for every seeded exercise using Claude Sonnet."""
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY must be set to generate drafts.")

    model = os.getenv("ANTHROPIC_SEED_MODEL", DEFAULT_SEED_MODEL)
    client = anthropic.Anthropic(api_key=api_key)
    exercises = _seed_exercise_ids()

    drafts = []
    for exercise in exercises:
        print(f"Generating draft for {exercise['id']}...")
        prompt = (
            "Write concise, high-quality strength-coaching content for the exercise "
            f'"{exercise["name"]}". Return ONLY a JSON object (no markdown fences, no '
            "surrounding prose) with exactly these keys: how_to, why_it_works, "
            "common_mistakes, beginner_notes. Each value should be 2-4 sentences of "
            "practical, technically accurate advice suitable for a fitness app."
        )
        response = client.messages.create(
            model=model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            print(f"  WARNING: could not parse JSON response for {exercise['id']}; storing raw text in how_to")
            parsed = {"how_to": text}

        drafts.append({"id": exercise["id"], **{f: parsed.get(f, "") for f in CONTENT_FIELDS}})

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DRAFT_PATH, "w") as f:
        json.dump({"exercises": drafts}, f, indent=2)

    print(f"\nWrote {len(drafts)} draft(s) to {DRAFT_PATH}")
    print("Adapt this draft to the CatalogEntry schema, then use exercise_catalog.py for review and publication.")


def cmd_upsert(args: argparse.Namespace) -> None:
    """Reject the retired direct-publishing command with migration guidance."""
    raise SystemExit("Direct content upsert is retired. Use scripts/exercise_catalog.py import, submit, review, and publish; filenames are not approvals.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate", help="Draft content via Claude Sonnet (requires ANTHROPIC_API_KEY)"
    )
    generate_parser.set_defaults(func=cmd_generate)

    upsert_parser = subparsers.add_parser("upsert", help="Retired: use exercise_catalog.py review and publish")
    upsert_parser.add_argument(
        "--file",
        help="Legacy argument accepted only to display migration guidance; no content is loaded",
    )
    upsert_parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Retired compatibility argument; cannot bypass actual review approvals",
    )
    upsert_parser.set_defaults(func=cmd_upsert)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
