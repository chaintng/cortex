"""Pinned Mem0 VectorStoreBase contract. All SQL is schema-qualified; never DDL."""
from types import SimpleNamespace
from uuid import UUID
from psycopg.types.json import Jsonb
from mem0.vector_stores.base import VectorStoreBase


def vector_literal(value):
    import math
    if len(value) != 1536 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in value):
        raise ValueError('Invalid embedding')
    return '[' + ','.join(str(float(x)) for x in value) + ']'


class PostgresVectors(VectorStoreBase):
    def __init__(self, database, tenant, generations, writable_generation=None):
        self.db, self.tenant = database, str(UUID(str(tenant)))
        self.generations = [str(UUID(str(g))) for g in generations]
        self.writable = str(UUID(str(writable_generation))) if writable_generation else None
        if not self.generations or (self.writable and self.writable not in self.generations):
            raise ValueError('Explicit generation scope required')

    def _scope(self):
        return 'tenant_id=%s::uuid AND generation_id=ANY(%s::uuid[])', [self.tenant, self.generations]

    def _filters(self, filters):
        conditions, args = [], []
        for key, value in (filters or {}).items():
            if key not in ('user_id', 'run_id', 'agent_id') or not isinstance(value, str):
                raise ValueError('Unsupported internal Mem0 filter')
            conditions.append('payload->>%s=%s')
            args.extend([key, value])
        return (' AND ' + ' AND '.join(conditions) if conditions else ''), args

    def insert(self, vectors, payloads=None, ids=None):
        if not self.writable or not payloads or not ids or len(vectors) != len(payloads) or len(ids) != len(vectors):
            raise ValueError('Invalid write')
        with self.db.transaction(self.tenant) as connection:
            for vector, payload, identifier in zip(vectors, payloads, ids):
                prefix = payload.get('data', '').split(':', 1)[0].lower()
                payload = {**payload, 'kind': {'episode': 'episode', 'commitment': 'commitment'}.get(prefix, 'fact')}
                connection.execute('INSERT INTO cortex.memory_vectors(id,vector,payload,tenant_id,generation_id) VALUES(%s,%s::extensions.vector,%s,%s,%s)',
                                   (str(UUID(str(identifier))), vector_literal(vector), Jsonb(payload), self.tenant, self.writable))

    def search(self, query, vectors, limit=5, filters=None):
        scope, args = self._scope()
        conditions, filter_args = self._filters(filters)
        with self.db.transaction(self.tenant) as connection:
            rows = connection.execute('SELECT id,payload,1-(vector <=> %s::extensions.vector) score FROM cortex.memory_vectors WHERE ' + scope + conditions + ' ORDER BY score DESC,id LIMIT %s',
                                      [vector_literal(vectors), *args, *filter_args, min(max(limit, 1), 20)]).fetchall()
        return [SimpleNamespace(id=str(r['id']), payload=r['payload'], score=float(r['score'])) for r in rows]

    def list(self, filters=None, limit=100):
        scope, args = self._scope()
        conditions, filter_args = self._filters(filters)
        with self.db.transaction(self.tenant) as connection:
            rows = connection.execute('SELECT id,payload FROM cortex.memory_vectors WHERE ' + scope + conditions + ' ORDER BY id LIMIT %s',
                                      [*args, *filter_args, min(limit or 100, 100)]).fetchall()
        return [[SimpleNamespace(id=str(r['id']), payload=r['payload']) for r in rows]]

    def get(self, vector_id):
        scope, args = self._scope()
        with self.db.transaction(self.tenant) as connection:
            row = connection.execute('SELECT id,payload FROM cortex.memory_vectors WHERE ' + scope + ' AND id=%s', [*args, str(UUID(str(vector_id)))]).fetchone()
        return SimpleNamespace(id=str(row['id']), payload=row['payload']) if row else None

    def delete(self, vector_id):
        if not self.writable:
            raise PermissionError('Read-only store')
        with self.db.transaction(self.tenant) as connection:
            connection.execute('DELETE FROM cortex.memory_vectors WHERE tenant_id=%s AND generation_id=%s AND id=%s',
                               (self.tenant, self.writable, str(UUID(str(vector_id)))))

    def update(self, vector_id, vector=None, payload=None):
        if not self.writable:
            raise PermissionError('Read-only store')
        with self.db.transaction(self.tenant) as connection:
            if vector is not None:
                connection.execute('UPDATE cortex.memory_vectors SET vector=%s::extensions.vector WHERE tenant_id=%s AND generation_id=%s AND id=%s',
                                   (vector_literal(vector), self.tenant, self.writable, str(UUID(str(vector_id)))))
            if payload is not None:
                connection.execute('UPDATE cortex.memory_vectors SET payload=%s WHERE tenant_id=%s AND generation_id=%s AND id=%s',
                                   (Jsonb(payload), self.tenant, self.writable, str(UUID(str(vector_id)))))

    def create_col(self, *args, **kwargs):
        raise PermissionError('DDL belongs to db-contracts')

    def delete_col(self):
        raise PermissionError('Collection deletion is forbidden')

    def reset(self):
        raise PermissionError('Reset is forbidden')

    def list_cols(self):
        return ['vectors']

    def col_info(self):
        return {'name': 'vectors'}


class PostgresHistory:
    def __init__(self, database, tenant, generation):
        self.db, self.tenant, self.generation = database, str(tenant), str(generation)

    def add_history(self, memory_id, old_memory, new_memory, event, created_at=None, updated_at=None, is_deleted=0, actor_id=None, role=None):
        with self.db.transaction(self.tenant) as connection:
            connection.execute('INSERT INTO cortex.memory_history(memory_id,tenant_id,generation_id,old_memory,new_memory,event,is_deleted,created_at,updated_at,actor_id,role) VALUES(%s,%s,%s,%s,%s,%s,%s,coalesce(%s::timestamptz,now()),%s,%s,%s)',
                               (memory_id, self.tenant, self.generation, old_memory, new_memory, event, bool(is_deleted), created_at, updated_at, actor_id, role))

    def get_history(self, memory_id):
        with self.db.transaction(self.tenant) as connection:
            rows = connection.execute('SELECT id,memory_id,old_memory,new_memory,event,is_deleted,created_at,updated_at,actor_id,role FROM cortex.memory_history WHERE tenant_id=%s AND generation_id=%s AND memory_id=%s ORDER BY created_at,id',
                                      (self.tenant, self.generation, memory_id)).fetchall()
        return rows

    def close(self):
        pass  # The invocation-scoped database owns the pool.
