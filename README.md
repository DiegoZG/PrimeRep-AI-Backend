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

---

### 6. Start the API server

uvicorn app.main:app --reload

API URL  
http://127.0.0.1:8000

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

## Authentication (MVP)

### Signup

POST /v1/auth/signup

Request body

{
"email": "test@primerep.com",
"password": "StrongPass123",
"preferred_name": "Diego",
"last_name": "Zegarra"
}

Response

{
"access_token": "<jwt>",
"token_type": "bearer"
}

---

### Login

POST /v1/auth/login

Returns a JWT access token on success.

---

## Environment Variables

Create a .env file (not committed to git):

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/primerep  
JWT_SECRET_KEY=your-secret-key  
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60

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
