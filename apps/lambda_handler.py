"""Compatibility dispatcher for the existing development Lambda functions."""
import os
from apps.api.handler import handle as api_handle
from apps.worker.handler import handle as worker_handle
from cortex.adapters.aws.runtime import configure
from cortex.adapters.postgres.database import Database


def lambda_handler(event, context):
    mode = os.environ['CI_MODE']
    if mode not in ('retrieval', 'intake', 'knowledge-intake', 'extraction', 'maintenance'):
        raise ValueError('Unsupported function mode')
    if (mode == 'retrieval') == ('Records' in event):
        raise ValueError('Operation is not allowed by this function')
    configure()
    database = Database()
    try:
        return (api_handle if mode == 'retrieval' else worker_handle)(database, event)
    finally:
        database.close()
