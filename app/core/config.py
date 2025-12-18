import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
APP_ENV = os.getenv("APP_ENV", "local")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Create a .env file or export it.")
