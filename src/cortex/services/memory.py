from uuid import UUID
from psycopg.types.json import Jsonb
from cortex.adapters.mem0.engine import SupabaseMemory, BudgetExhausted
from cortex.adapters.mem0.synthesis import synthesize, embed_text
from cortex.adapters.postgres.conversations import ConversationEvidence
from cortex.adapters.postgres.storage import vector_literal
from cortex.contracts.synthesis import config_hash, validate_outputs
from cortex.authorization.scope import read_scope


def extract(database, event_id, evidence=None, synthesizer=None):
    claim = database.call('claim', event_id)
    if claim['status'] in ('busy', 'early'):
        raise RuntimeError('Extraction wakeup cannot be acknowledged yet')
    if claim['status'] != 'claimed':
        return claim['status']
    token, tenant = claim['lease_token'], claim['tenant_id']
    try:
        selected, digest, coverage = (evidence or ConversationEvidence(database)).load(tenant, claim['conversation_id'])
        enabled, profiles = claim['outputs_enabled'], claim['profiles']
        prepared = database.call('prepare_synthesis', event_id, token, claim['revision'], digest,
                                 config_hash(enabled, profiles), Jsonb(selected), Jsonb(coverage))
        if prepared['status'] != 'build':
            return prepared['status']
        outputs = prepared['outputs']
        if outputs is None:
            outputs = validate_outputs((synthesizer or synthesize)(database, tenant, selected, enabled, profiles), selected, enabled, profiles)
            # Save proposals before embeddings. A failed memory write reuses this output.
            outputs = database.call('save_synthesis', event_id, token, prepared['run_id'], Jsonb(outputs))
        if enabled['memory'] and not prepared['memory_done']:
            build = database.call('prepare_memory', event_id, token, prepared['run_id'])
            if build['status'] == 'build':
                generation = build['generation_id']
                if outputs['memories']:
                    memory = SupabaseMemory(database, tenant, [generation], generation)
                    for item in outputs['memories']:
                        memory.add(item['kind'].upper() + ': ' + item['content'], user_id=tenant, run_id=generation,
                                   metadata={'conversation_id': claim['conversation_id'], 'source_ids': item['source_ids']}, infer=False)
                    memory.assert_success()
        return database.call('finish_synthesis', event_id, token, prepared['run_id'])
    except Exception as error:
        budget = isinstance(error, BudgetExhausted)
        database.call('fail', event_id, token, budget)
        if budget:
            return 'budget_deferred'
        raise


def publish(database, event_id, embedder=None):
    from cortex.services.publication import index_publication
    return index_publication(database, event_id, embedder or embed_text)


def retrieve(database, request):
    tenant = str(UUID(request['tenant_id']))
    conversation = request['conversation_id']
    actor = str(UUID(request['actor_user_id'])) if request.get('actor_user_id') else None
    query = str(request.get('query', '')).strip()[:2000]
    operation = request['operation']
    if operation in ('knowledge_search', 'lesson_search'):
        # Authorization precedes embedding; callers cannot select a raw namespace.
        database.call('authorize', tenant, conversation, actor)
        if not query:
            return {'items': []}
        skill = request.get('skill_code') if operation == 'lesson_search' else None
        if operation == 'lesson_search' and skill != 'kj_sales':
            raise ValueError('Unsupported learning skill')
        return database.call('search_published', tenant, conversation, vector_literal(embed_text(query)), skill, actor)
    tenant, conversation, actor, scope = read_scope(database, request)
    if operation == 'list':
        return database.call('ci_memory_read', tenant, conversation, tenant=tenant, actor=actor, schema='messaging')
    if not query or not scope['generations']:
        return {'items': []}
    memory = SupabaseMemory(database, tenant, scope['generations'])
    found = memory.search(query, user_id=tenant, limit=10, rerank=False)['results']
    return database.call('search_results', tenant, conversation, False,
                         Jsonb([{'id': r['id'], 'score': r.get('score', 0)} for r in found]), actor)
