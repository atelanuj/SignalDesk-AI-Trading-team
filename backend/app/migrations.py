"""Small, versioned additive PostgreSQL migrations for existing local databases."""
from sqlalchemy import text

MIGRATIONS = [
    ('20261108_paper_exit_levels', [
        'ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(20, 4)',
        'ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS target_price NUMERIC(20, 4)',
    ]),
]


def apply_migrations(connection):
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('signaldesk_schema_migrations'))"))
    connection.execute(text('CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())'))
    for version, statements in MIGRATIONS:
        if connection.execute(text('SELECT 1 FROM schema_migrations WHERE version = :version'), {'version': version}).scalar():
            continue
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(text('INSERT INTO schema_migrations (version) VALUES (:version)'), {'version': version})
