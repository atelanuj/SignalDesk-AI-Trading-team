"""Run integration checks in a disposable schema, never the user's journal."""
import os
import re
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text


def main():
    engine = create_engine(os.environ['DATABASE_URL'])
    schema = 'signaldesk_test_' + uuid4().hex
    assert re.fullmatch(r'signaldesk_test_[a-f0-9]{32}', schema)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    try:
        env = dict(os.environ)
        env['DATABASE_URL'] = engine.url.update_query_dict({'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
        env['TEST_ISOLATED'] = '1'
        return subprocess.call([sys.executable, '-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider', *sys.argv[1:]], env=env)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
