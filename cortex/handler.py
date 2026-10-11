import json
import logging
import time
from uuid import UUID
from psycopg.types.json import Jsonb
from .database import Database
from .engine import SupabaseMemory, BudgetExhausted
from .evidence import bounded_evidence, MEMORY_PROMPT, KNOWLEDGE_PROMPT, EXTRACTION_CONFIG

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def extract(database, event_id):
    claim = database.call('claim', event_id)
    if claim['status'] in ('busy', 'early'):
        raise RuntimeError('Retry work with an active lease or future deadline')
    if claim['status'] != 'claimed':
        return claim['status']
    token, tenant, generation = claim['lease_token'], claim['tenant_id'], None
    try:
        records = database.call('evidence', tenant, claim['conversation_id'])
        selected, digest, coverage = bounded_evidence(records)
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
    tenant, conversation = str(UUID(request['tenant_id'])), request['conversation_id']
    actor = str(UUID(request['actor_user_id'])) if request.get('actor_user_id') else None
    query = str(request.get('query', '')).strip()[:2000]
    operation = request['operation']
    scope = database.call('read_scope', tenant, conversation, operation == 'knowledge_search', actor)
    if operation == 'list':
        return database.call('ci_memory_read', tenant, conversation, tenant=tenant, actor=actor, schema='messaging')
    if not query or not scope['generations']:
        return {'items': []}
    memory = SupabaseMemory(database, tenant, scope['generations'])
    found = memory.search(query, user_id=tenant, limit=10, rerank=False)['results']
    # Revalidate permissions and active generations after the embedding/network call.
    return database.call('search_results', tenant, conversation, operation == 'knowledge_search',
                         Jsonb([{'id': r['id'], 'score': r.get('score', 0)} for r in found]), actor)


def handle(database, event):
    if 'Records' in event:
        failures = []
        for record in event['Records']:
            started = time.monotonic()
            operation = 'decode'
            try:
                reference = json.loads(record['body'])
                event_id = str(UUID(reference['event_id']))
                # Only the durable DB event determines operation and scope.
                operation = database.call('event_operation', event_id)
                if operation == 'intake':
                    database.call('intake', event_id, __import__('os').environ['CI_PIPELINE'])
                elif operation == 'extract':
                    extract(database, event_id)
                elif operation == 'publish':
                    publish(database, event_id)
                elif operation == 'cleanup':
                    database.call('cleanup', event_id)
                else:
                    raise ValueError('Unsupported durable event')
            except Exception as error:
                # No transcript, model output, SQL text, DSN or customer IDs in logs.
                logger.error('ci_queue_failure type=%s', type(error).__name__)
                failures.append({'itemIdentifier': record['messageId']})
            finally:
                logger.info(json.dumps({'metric': 'queue_operation', 'operation': operation, 'duration_ms': round((time.monotonic()-started)*1000)}))
        return {'batchItemFailures': failures}
    if event.get('operation') == 'health':
        # IAM-only provisioning check: no tenant data, embeddings or model calls.
        with database.transaction() as connection:
            ready = connection.execute("SELECT pg_has_role(current_user, 'ci_worker', 'member') AS ready").fetchone()['ready']
        if not ready:
            raise RuntimeError('Restricted database role is unavailable')
        return {'status': 'ready', 'storage': 'postgresql', 'model_called': False}
    if event.get('operation') not in ('search', 'knowledge_search', 'list'):
        raise ValueError('Unsupported retrieval operation')
    started = time.monotonic()
    try:
        return retrieve(database, event)
    finally:
        logger.info(json.dumps({'metric': 'retrieval', 'operation': event['operation'], 'duration_ms': round((time.monotonic()-started)*1000)}))


def lambda_handler(event, context):
    import os
    mode = os.environ["CI_MODE"]
    if (mode == "retrieval") == ("Records" in event):
        raise ValueError("Operation is not allowed by this function")
    from .runtime import configure
    configure()
    database = Database()
    try:
        return handle(database, event)
    finally:
        database.close()
