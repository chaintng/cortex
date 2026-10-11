from psycopg.types.json import Jsonb
from cortex.adapters.mem0.engine import SupabaseMemory, BudgetExhausted
from cortex.adapters.postgres.conversations import ConversationEvidence
from cortex.contracts.evidence import MEMORY_PROMPT, KNOWLEDGE_PROMPT, EXTRACTION_CONFIG
from cortex.authorization.scope import read_scope


def extract(database, event_id, evidence=None):
    claim = database.call('claim', event_id)
    if claim['status'] in ('busy', 'early'):
        raise RuntimeError('Retry work with an active lease or future deadline')
    if claim['status'] != 'claimed':
        return claim['status']
    token, tenant, generation = claim['lease_token'], claim['tenant_id'], None
    try:
        selected, digest, coverage = (evidence or ConversationEvidence(database)).load(tenant, claim['conversation_id'])
        prepared = database.call('prepare', event_id, token, claim['revision'], digest,
                                 EXTRACTION_CONFIG + ':' + claim['pipeline'], Jsonb(selected), Jsonb(coverage))
        if prepared['status'] != 'build':
            return prepared['status']
        generation = prepared['generation_id']
        if selected:
            memory = SupabaseMemory(database, tenant, [generation], generation,
                                    MEMORY_PROMPT if claim['pipeline'] == 'memory' else KNOWLEDGE_PROMPT)
            memory.add([{'role': r['role'], 'content': r['content']} for r in reversed(selected)],
                       user_id=tenant, run_id=generation, metadata={'conversation_id': claim['conversation_id']}, infer=True)
            memory.assert_success()
        return database.call('finish', event_id, token, generation)
    except Exception as error:
        if generation:
            database.call('abandon', event_id, generation)
        budget = isinstance(error, BudgetExhausted)
        database.call('fail', event_id, token, budget)
        if budget:
            return 'budget_deferred'
        raise


def publish(database, event_id):
    prepared = database.call('prepare_publication', event_id)
    if prepared['status'] != 'build':
        return prepared['status']
    generation, tenant = prepared['generation_id'], prepared['tenant_id']
    try:
        memory = SupabaseMemory(database, tenant, [generation], generation)
        memory.add(prepared['content'], user_id=tenant, run_id=generation,
                   metadata={'knowledge_id': prepared['knowledge_id']}, infer=False)
        memory.assert_success()
        return database.call('finish_publication', event_id, generation)
    except Exception:
        database.call('abandon', event_id, generation)
        raise


def retrieve(database, request):
    tenant, conversation, actor, scope = read_scope(database, request)
    query = str(request.get('query', '')).strip()[:2000]
    operation = request['operation']
    if operation == 'list':
        return database.call('ci_memory_read', tenant, conversation, tenant=tenant, actor=actor, schema='messaging')
    if not query or not scope['generations']:
        return {'items': []}
    memory = SupabaseMemory(database, tenant, scope['generations'])
    found = memory.search(query, user_id=tenant, limit=10, rerank=False)['results']
    # Revalidate permissions and active generations after the embedding/network call.
    return database.call('search_results', tenant, conversation, operation == 'knowledge_search',
                         Jsonb([{'id': r['id'], 'score': r.get('score', 0)} for r in found]), actor)


