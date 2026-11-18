"""One-off: apply the schema and seed control state on a hosted Postgres (e.g. Neon).

Set POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
POSTGRES_SSLMODE=require and POSTGRES_CONNECT_TIMEOUT=15 in the environment, then run:
    python init_remote_db.py
"""
from app import control, db

if not db.wait_for_db(60):
    raise SystemExit("database not reachable")
db.apply_schema()
control.seed_if_empty()
print("schema applied and control state seeded")
