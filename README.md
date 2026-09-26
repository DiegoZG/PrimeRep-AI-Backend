# PrimeRep Backend

Backend API for PrimeRep, an AI-powered fitness coaching application.  
This service provides authentication, user management, and the foundation for workouts, progress tracking, and an AI coach.

---

## Tech Stack

- Python 3.9
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- JWT Authentication
- passlib + bcrypt
- Docker Compose

---

## Project Structure

primerep-backend/

- app/
  - api/
    - v1/
      - router.py
      - auth/
        - router.py
  - core/
    - config.py
    - settings.py
    - database.py
    - user_service.py
    - security/
      - jwt.py
      - passwords.py
  - models/
    - user.py
    - **init**.py
  - schemas/
    - auth.py
  - main.py
- alembic/
  - versions/
  - env.py
  - script.py.mako
- alembic.ini
- docker-compose.yml
- requirements.txt
- README.md

---

## Local Development Setup

### 1. Clone the repository

git clone <repo-url>  
cd primerep-backend

---

### 2. Create and activate a virtual environment

python3 -m venv .venv  
source .venv/bin/activate

---

### 3. Install dependencies

python3 -m pip install -r requirements.txt

---

### 4. Start PostgreSQL

docker compose up -d

---

### 5. Run database migrations

alembic upgrade head

The workout-program search migration enables PostgreSQL's trusted `pg_trgm`
extension. In production, install it ahead of deployment or grant the migration
role permission to run `CREATE EXTENSION IF NOT EXISTS pg_trgm`.

---

### 6. Start the API server

uvicorn app.main:app --reload

API URL  
http://127.0.0.1:8000

### Coach notification worker

Run the durable Coach notification outbox every five minutes from the deployment
platform's cron scheduler:

```bash
python scripts/run_coach_worker.py
```

The command reconciles enabled users, claims due jobs with PostgreSQL
`FOR UPDATE SKIP LOCKED`, sends Expo push tickets in batches of at most 100,
polls mature receipts, retries transient failures, removes unregistered device
tokens, and purges Coach data after its 30-day retention window. Set the optional
`EXPO_ACCESS_TOKEN` environment variable for authenticated Expo push requests.

### Coach Maestro fixture

After migrations and canonical exercise/program seeds are installed, create the
deterministic local fixture used by `.maestro/seeded/coach-feed.yaml`:

```bash
APP_ENV=local PYTHONPATH=. .venv/bin/python scripts/seed_coach_e2e_fixture.py
```

The command is idempotent and replaces only `coach-fixture@example.com` plus
its cascaded fixture data. It refuses to run outside explicit local, development,
or test environments. It does not print credentials, password hashes, or tokens.
The matching password remains documented only in the mobile Maestro README.

Remove the fixture safely with:

```bash
APP_ENV=local PYTHONPATH=. .venv/bin/python scripts/seed_coach_e2e_fixture.py --teardown
```

API Docs  
http://127.0.0.1:8000/docs

---

## API Endpoints

### Equipment Catalog (Public)

GET /v1/equipment

Returns all active equipment items, ordered by category_order, sort_order, then name.

Response

```json
{
  "items": [
    {
      "id": "ab_crunch_machine",
      "name": "Ab Crunch Machine",
      "category": "machine",
      "category_order": 10,
      "sort_order": 0,
      "icon_key": null,
      "image_url": null,
      "is_active": true
    }
  ]
}
```

---

### Exercise Catalog (Public)

GET /v1/exercises

Returns all active exercises, filterable via query parameters:

- `q`: free-text search on `name` and `id`
- `muscle`: primary or secondary muscle (canonical strings)
- `equipment_id`: required equipment id
- `type`: exercise type (`strength`, `bodyweight`, `cardio`, `mobility`, `olympic`, `accessory`)
- `limit`, `offset`: simple pagination

Example:

```bash
curl "http://127.0.0.1:8000/v1/exercises?q=press&muscle=chest&equipment_id=flat_bench&type=strength"
```

Sample response:

```json
{
  "items": [
    {
      "id": "bench_press",
      "name": "Bench Press",
      "exercise_type": "strength",
      "primary_muscle": "chest",
      "secondary_muscles": ["triceps", "shoulders"],
      "required_equipment_ids": ["flat_bench", "olympic_barbell", "plates"],
      "demo_video_url": null,
      "image_url": null,
      "is_active": true,
      "is_favorited": false
    }
  ]
}
```

GET /v1/exercises/{id}

Returns details for a single exercise by id.

Canonical muscle strings:

`chest, shoulders, back, biceps, triceps, quads, hamstrings, glutes, calves, abs, forearms, neck, cardio, full_body`

Exercise types:

`strength, bodyweight, cardio, mobility, olympic, accessory`

---

### Exercise Favorites (Authenticated)

These endpoints require a valid `Authorization: Bearer <access_token>` header.

Favorite an exercise:

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8000/v1/exercises/bench_press/favorite"
```

Unfavorite:

```bash
curl -X DELETE \
  -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8000/v1/exercises/bench_press/favorite"
```

List favorites:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8000/v1/exercises/favorites"
```

Response shape matches the exercise catalog list, with `is_favorited` set to `true` on all items.

---

### Explore Programs (Authenticated)

`GET /v1/workout-templates` lists system and owned private programs. Program
activation uses `POST /v1/workout-templates/{templateId}/activate`; its
`clientOperationId` is permanently bound to the complete canonical request, so
reusing that key with different activation inputs returns `409`.

`GET /v1/workout-templates/active?effectiveDate=YYYY-MM-DD` promotes a scheduled
revision only when `effectiveDate` is the caller's current local date. The API
accepts UTC today plus or minus one day for timezone boundaries and rejects
arbitrary future dates.

`PATCH /v1/users/me/training-preferences` keeps its preference fields in the JSON
body. Updated clients also send `weekStart` and `effectiveDate` query parameters:

```text
PATCH /v1/users/me/training-preferences?weekStart=2026-09-14&effectiveDate=2026-09-14
```

The parameters must be supplied together. `effectiveDate` is the caller's local
today and `weekStart` identifies the containing Monday. Older clients may omit
both and use the server's current UTC week.

---

## Authentication (MVP)

### Signup

POST /v1/auth/signup

Request body

{
"email": "test@primerep.com",
"password": "StrongPass123",
"preferred_name": "Diego",
"last_name": "Zegarra",
"legalAcceptance": {
  "accepted": true,
  "termsVersion": "2026-09-12",
  "privacyVersion": "2026-09-12"
}
}

Response

{
"access_token": "<jwt>",
"refresh_token": "<jwt>",
"token_type": "bearer"
}

---

### Login

POST /v1/auth/login

Returns a JWT access token on success.

### Password reset

`POST /v1/auth/password-reset/request` accepts `{ "email": "user@example.com" }`
and always returns a generic `202` response. `POST /v1/auth/password-reset/confirm`
accepts `{ "token": "<opaque-token>", "newPassword": "..." }` and returns `204`
when the single-use reset link is valid.

Reset and password-change emails are persisted in an encrypted database outbox
in the same transaction as the account change. A background task attempts
immediate delivery; run `python scripts/run_email_worker.py` every minute to
retry after API restarts or provider outages. Reset links are never delivered
after expiry or invalidation. Payloads are cleared after send, expiry, or the
eighth failed attempt. Production requires a stable, separate Fernet key in
`EMAIL_OUTBOX_ENCRYPTION_KEY`; generate one with
`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
and keep it in the secret store. Changing this key while messages are pending
makes those messages unreadable, so drain or re-encrypt before rotation.

---

## Environment Variables

Create an uncommitted `.env` from `.env.example`. The active setting names are
`JWT_SECRET` and `JWT_REFRESH_SECRET`; `JWT_SECRET_KEY` is not used. For preview
and production requirements, preflight, workers, backup, and rollback, see
[Part 9 release operations](docs/part9-release-operations.md).

---

## Database Migrations

Create a new migration

alembic revision --autogenerate -m "description"

Apply migrations

alembic upgrade head

---

## Current Status

- Backend server running
- PostgreSQL connected
- User signup implemented
- User login implemented
- Password hashing with bcrypt
- JWT authentication working

---

## Roadmap

- Protected user routes
- Workout generation and logging
- AI coach (text, then chat/voice)
- Exercise demo media
- Payments and subscriptions
- Push notifications

---

## Notes

- .venv, .env, and database volumes are ignored via .gitignore
- Designed to integrate with an Expo / React Native frontend

---

## License

Private / Proprietary
