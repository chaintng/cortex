"""SQS adapter. Durable events determine operation and scope."""
import json
import logging
import time
from uuid import UUID
from cortex.services.memory import extract, publish

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handle(database, event):
    if 'Records' not in event:
        raise ValueError('Worker requires SQS records')
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
