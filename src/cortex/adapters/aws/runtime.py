"""Load narrowly scoped runtime secrets without logging their contents."""
import os
import json
from functools import lru_cache
import boto3
from botocore.exceptions import ClientError


def _raw_secret(client, arn, optional=False):
    try:
        value = client.get_secret_value(SecretId=arn)['SecretString']
    except ClientError as error:
        if optional and error.response.get('Error', {}).get('Code') == 'ResourceNotFoundException':
            return None
        raise
    if not value.strip():
        if optional:
            return None
        raise ValueError('Expected a dedicated string secret')
    if value.lstrip().startswith('{'):
        raise ValueError('Expected a dedicated string secret')
    return value


@lru_cache(maxsize=1)
def configure():
    shared_name = os.environ.get('KJ_CORTEX_SECRET_NAME')
    if shared_name:
        client = boto3.client('secretsmanager')
        raw = client.get_secret_value(SecretId=shared_name)['SecretString']
        try:
            values = json.loads(raw)
        except (ValueError, TypeError):
            raise ValueError('Invalid Cortex shared secret pack') from None
        if not isinstance(values, dict):
            raise ValueError('Invalid Cortex shared secret pack')
        def value(name):
            result = values.get(name)
            if result is None:
                return None
            if not isinstance(result, str):
                raise ValueError('Invalid Cortex shared secret key type')
            return result.strip() or None
        database = value('KJ_CORTEX_DATABASE_URL')
        key = value('OPENAI_KJ_CORTEX_API_KEY') or value('OPENAI_API_KEY')
        if not database or not key:
            raise RuntimeError('Missing required Cortex shared secret keys')
        # Validate the complete pack before changing process configuration. Never
        # fall through to legacy credentials when a configured pack is broken.
        os.environ['KJ_CORTEX_DATABASE_URL'] = database
        os.environ['OPENAI_API_KEY'] = key
        return

    client = None
    database_arn = os.environ.get('CI_DATABASE_SECRET_ARN')
    if database_arn:
        client = boto3.client('secretsmanager')
        os.environ['KJ_CORTEX_DATABASE_URL'] = _raw_secret(client, database_arn)
    elif not os.environ.get('KJ_CORTEX_DATABASE_URL'):
        raise RuntimeError('Missing runtime configuration: CI_DATABASE_SECRET_ARN')

    dedicated_arn = os.environ.get('CI_CORTEX_OPENAI_SECRET_ARN')
    key = os.environ.get('OPENAI_KJ_CORTEX_API_KEY', '').strip() or None
    if dedicated_arn:
        client = client or boto3.client('secretsmanager')
        key = _raw_secret(client, dedicated_arn, optional=True)
    if not key:
        fallback_arn = os.environ.get('CI_OPENAI_SECRET_ARN')
        if fallback_arn:
            client = client or boto3.client('secretsmanager')
            key = _raw_secret(client, fallback_arn)
        else:
            key = os.environ.get('OPENAI_API_KEY', '').strip() or None
    if not key:
        raise RuntimeError('Missing runtime configuration: CI_OPENAI_SECRET_ARN')
    os.environ['OPENAI_API_KEY'] = key
