import json
from uuid import uuid4
import pytest
from cortex.adapters.postgres.database import Database
from cortex.adapters.mem0.engine import SupabaseMemory, BudgetedModel, BudgetExhausted
from cortex.contracts.evidence import bounded_evidence
from cortex.adapters.postgres.storage import vector_literal


class FakeEmbedding:
    def embed(self, text, memory_action=None):
        return [1.0] + [0.0] * 1535


class FakeModel:
    config = type('Config', (), {'model': 'fake'})()
    def generate_response(self, *args, **kwargs):
        messages = json.dumps(args or kwargs, ensure_ascii=False)
        if 'old_memory' in messages or 'ADD' in messages:
            return json.dumps({'memory': [{'id': '0', 'text': 'FACT: ลูกค้าชอบสีน้ำเงิน', 'event': 'ADD'}]})
        return json.dumps({'facts': ['FACT: ลูกค้าชอบสีน้ำเงิน']})


def test_bounds_skip_oversized_sources():
    class Encoding:
        def encode(self, text): return list(text)
    rows = [{'source_id': str(uuid4()), 'source_kind': 'message', 'source_hash': 'x', 'content': 'x' * 13000}]
    selected, digest, coverage = bounded_evidence(rows, Encoding())
    assert selected == [] and coverage['source_tokens'] == 0 and not coverage['complete_history']


def test_budget_is_reserved_before_every_call():
    class Budget:
        calls = 0
        def call(self, *args):
            self.calls += 1
            return self.calls < 2
    budget = Budget()
    model = BudgetedModel(FakeModel(), budget, str(uuid4()))
    model.generate_response()
    with pytest.raises(BudgetExhausted): model.generate_response()
    assert budget.calls == 2 and isinstance(model.failure, BudgetExhausted)


def test_vector_dimension_and_finite_guard():
    with pytest.raises(ValueError): vector_literal([0] * 3)
    with pytest.raises(ValueError): vector_literal([float('nan')] * 1536)


def test_mem0_real_add_search_and_persistent_history():
    import os
    url = os.environ.get('CI_TEST_DATABASE_URL')
    if not url: pytest.skip('Disposable database URL required')
    db, tenant, generation = Database(url), str(uuid4()), str(uuid4())
    with db.transaction(tenant) as connection:
        connection.execute("INSERT INTO cortex.memory_generations(generation_id,tenant_id,conversation_id,pipeline,source_revision,evidence_hash,config_hash) VALUES(%s,%s,'test','memory',1,'e','c')", (generation, tenant))
    try:
        memory = SupabaseMemory(db, tenant, [generation], generation, fake_llm=FakeModel(), fake_embedder=FakeEmbedding())
        result = memory.add('ลูกค้าชอบสีน้ำเงิน', user_id=tenant, run_id=generation)
        assert result['results'], result
        identifier = result['results'][0]['id']
        assert memory.history(identifier)[0]['new_memory'] == 'FACT: ลูกค้าชอบสีน้ำเงิน'
        memory.close()
        reader = SupabaseMemory(db, tenant, [generation], fake_llm=FakeModel(), fake_embedder=FakeEmbedding())
        assert reader.search('สีที่ชอบ', user_id=tenant, limit=5)['results'][0]['id'] == identifier
        foreign = SupabaseMemory(db, str(uuid4()), [generation], fake_llm=FakeModel(), fake_embedder=FakeEmbedding())
        assert foreign.search('สี', user_id=tenant)['results'] == []
        with pytest.raises(PermissionError): reader.vector_store.create_col()
        class NeverExtract:
            def generate_response(self, *args, **kwargs): raise AssertionError('Approved text must not be rewritten')
        memory.llm = NeverExtract()
        approved = 'Reviewed guidance: สาธิตสินค้า\nKeep this exact approved text.'
        publication = memory.add(approved, user_id=tenant, run_id=generation, infer=False)
        assert publication['results'][0]['memory'] == approved
        assert memory.history(publication['results'][0]['id'])[0]['new_memory'] == approved
    finally:
        with db.transaction(tenant) as connection:
            connection.execute('DELETE FROM cortex.memory_generations WHERE generation_id=%s', (generation,))
        db.close()

def test_runtime_role_cannot_cross_tenant_or_create_tables():
    import os
    import psycopg
    import pytest
    url = os.environ.get('CI_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Disposable migrated task database required')
    from urllib.parse import urlsplit
    if urlsplit(url).hostname not in ("127.0.0.1", "localhost"):
        pytest.skip("Role-switch checks are restricted to disposable localhost databases")
    admin_url = url.replace("postgres:postgres@", "supabase_admin:postgres@")
    with psycopg.connect(admin_url) as connection:
        connection.execute('SET LOCAL ROLE ci_worker')
        connection.execute("SELECT set_config('ci.tenant_id','96000000-0000-4000-8000-000000000001',true)")
        assert connection.execute("SELECT count(*) FROM cortex.memory_vectors WHERE tenant_id='96000000-0000-4000-8000-000000000002'").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute('CREATE TABLE cortex.forbidden_runtime_ddl(id int)')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute('SELECT * FROM cortex.knowledge')

def test_embedding_failures_remain_latched_and_upstream_text_logs_are_disabled():
    import logging
    from cortex.adapters.mem0.engine import MeasuredEmbedding
    class BrokenEmbedding:
        def embed(self, *args, **kwargs): raise RuntimeError('provider failed')
    measured = MeasuredEmbedding(BrokenEmbedding())
    with pytest.raises(RuntimeError): measured.embed('private customer evidence')
    assert isinstance(measured.failure, RuntimeError)
    assert logging.getLogger('mem0.memory.main').disabled
    assert logging.getLogger('mem0.memory.utils').disabled
