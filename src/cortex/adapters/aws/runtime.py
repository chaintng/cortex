"""Load narrowly scoped runtime secrets without logging their contents."""
import os
from functools import lru_cache
import boto3


@lru_cache(maxsize=1)
def configure():
    client = None
    for variable, arn_variable in (('CI_DATABASE_URL', 'CI_DATABASE_SECRET_ARN'),
                                   ('OPENAI_API_KEY', 'CI_OPENAI_SECRET_ARN')):
        arn = os.environ.get(arn_variable)
        if not arn:
            if not os.environ.get(variable):
                raise RuntimeError(f'Missing runtime configuration: {arn_variable}')
            continue
        client = client or boto3.client('secretsmanager')
        value = client.get_secret_value(SecretId=arn)['SecretString']
        # Dedicated secrets contain one raw string, never an application secret pack.
        if not value.strip() or value.lstrip().startswith('{'):
            raise ValueError('Expected a dedicated string secret')
        os.environ[variable] = value
