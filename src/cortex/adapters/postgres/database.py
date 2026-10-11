import os
from contextlib import contextmanager
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url=None):
        self.failure = None
        self.pool = ConnectionPool(url or os.environ['KJCORTEX_DATABASE_URL'], min_size=0, max_size=3, open=True,
                                   kwargs={'row_factory': dict_row, 'connect_timeout': 5}, timeout=5)

    @contextmanager
    def transaction(self, tenant=None, actor=None):
        with self.pool.connection() as connection, connection.transaction():
            connection.execute("SET LOCAL statement_timeout='3s'")
            connection.execute("SELECT set_config('ci.tenant_id',%s,true)", (str(tenant or ''),))
            role = 'authenticated' if actor else 'service_role'
            connection.execute("SELECT set_config('request.jwt.claim.role',%s,true)", (role,))
            connection.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor or ''),))
            try:
                yield connection
            except Exception as error:
                self.failure = error
                raise

    def call(self, name, *args, tenant=None, actor=None, schema='cortex'):
        with self.transaction(tenant, actor) as connection:
            query = sql.SQL('SELECT {}.{}({}) AS value').format(
                sql.Identifier(schema), sql.Identifier(name), sql.SQL(',').join(sql.Placeholder() for _ in args))
            return connection.execute(query, args).fetchone()['value']

    def close(self):
        self.pool.close()
