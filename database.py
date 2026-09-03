import os
import uuid

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. Please add your PostgreSQL connection string to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


DATABASE_URL = get_database_url()

# Separate connection from the LangGraph checkpointer's connection in backend.py.
# Keeping auth/trip data on its own connection avoids coupling it to graph internals.
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row
)


def init_db():
    """Create the users and trips tables if they don't already exist. Called on app startup."""
    with _conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT,
                hashed_password TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                thread_id TEXT NOT NULL,
                title TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_trips_user_id ON trips(user_id);
        """)


def create_user(email: str, name: str, hashed_password: str) -> dict:
    user_id = uuid.uuid4().hex

    with _conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (id, email, name, hashed_password)
            VALUES (%s, %s, %s, %s)
            RETURNING id, email, name, created_at;
            """,
            (user_id, email.lower().strip(), name, hashed_password)
        )
        return cur.fetchone()


def get_user_by_email(email: str):
    with _conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM users WHERE email = %s;",
            (email.lower().strip(),)
        )
        return cur.fetchone()


def get_user_by_id(user_id: str):
    with _conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, name, created_at FROM users WHERE id = %s;",
            (user_id,)
        )
        return cur.fetchone()


def save_trip(user_id: str, thread_id: str, title: str):
    """Link a thread_id to a user, once. Safe to call on every /api/travel request."""
    with _conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM trips WHERE user_id = %s AND thread_id = %s;",
            (user_id, thread_id)
        )
        existing = cur.fetchone()
        if existing:
            return existing

        trip_id = uuid.uuid4().hex
        cur.execute(
            """
            INSERT INTO trips (id, user_id, thread_id, title)
            VALUES (%s, %s, %s, %s)
            RETURNING id, thread_id, title, created_at;
            """,
            (trip_id, user_id, thread_id, title)
        )
        return cur.fetchone()


def get_user_trips(user_id: str):
    with _conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, thread_id, title, created_at::text AS created_at
            FROM trips
            WHERE user_id = %s
            ORDER BY created_at DESC;
            """,
            (user_id,)
        )
        return cur.fetchall()
