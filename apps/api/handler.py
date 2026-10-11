"""IAM-only Lambda API adapter; no public HTTP endpoint."""
import json
import logging
import time
from cortex.services.memory import retrieve

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handle(database, event):
    if 'Records' in event:
        raise ValueError('API does not accept queue records')
    if event.get('operation') == 'health':
        # IAM-only provisioning check: no tenant data, embeddings or model calls.
        with database.transaction() as connection:
            ready = connection.execute("SELECT pg_has_role(current_user, 'ci_worker', 'member') AND to_regclass('cortex.synthesis_runs') IS NOT NULL AND to_regclass('cortex.memory_vectors') IS NOT NULL AS ready").fetchone()['ready']
        if not ready:
            raise RuntimeError('Restricted database role is unavailable')
        return {'status': 'ready', 'storage': 'postgresql', 'model_called': False}
    if event.get('operation') not in ('search', 'knowledge_search', 'lesson_search', 'list'):
        raise ValueError('Unsupported retrieval operation')
    started = time.monotonic()
    try:
        return retrieve(database, event)
    finally:
        logger.info(json.dumps({'metric': 'retrieval', 'operation': event['operation'], 'duration_ms': round((time.monotonic()-started)*1000)}))

