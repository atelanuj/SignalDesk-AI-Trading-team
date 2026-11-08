from uuid import uuid4
from sqlalchemy import text

from app.db import engine
from app.migrations import apply_migrations, MIGRATIONS


def test_exit_level_migration_preserves_old_records_and_is_repeatable():
    schema = 'paper_level_test_' + uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
            connection.execute(text(f'SET LOCAL search_path TO {schema}'))
            connection.execute(text('CREATE TABLE paper_trades (id TEXT PRIMARY KEY, entry_price NUMERIC(20, 4))'))
            connection.execute(text("INSERT INTO paper_trades VALUES ('existing-trade', 100.25)"))
            apply_migrations(connection)
            row = connection.execute(text('SELECT id, entry_price, stop_loss, target_price FROM paper_trades')).one()
            assert row.id == 'existing-trade' and str(row.entry_price) == '100.2500'
            assert row.stop_loss is None and row.target_price is None
            connection.execute(text('UPDATE paper_trades SET stop_loss = 95, target_price = 115'))
            apply_migrations(connection)
            row = connection.execute(text('SELECT stop_loss, target_price FROM paper_trades')).one()
            assert str(row.stop_loss) == '95.0000' and str(row.target_price) == '115.0000'
            assert connection.execute(text('SELECT COUNT(*) FROM schema_migrations')).scalar() == len(MIGRATIONS)
        finally:
            transaction.rollback()
