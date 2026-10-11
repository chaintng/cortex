from unittest.mock import Mock
import pytest
from botocore.exceptions import ClientError
from cortex.adapters.aws.runtime import configure


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    configure.cache_clear()
    for key in ['KJ_CORTEX_SECRET_NAME', 'CI_DATABASE_SECRET_ARN', 'CI_CORTEX_OPENAI_SECRET_ARN', 'OPENAI_KJ_CORTEX_API_KEY', 'OPENAI_API_KEY']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('KJ_CORTEX_DATABASE_URL', 'test-database')
    monkeypatch.setenv('CI_OPENAI_SECRET_ARN', 'fallback')
    yield
    configure.cache_clear()


@pytest.mark.parametrize('primary', ['dedicated-key', '', '   ', None])
def test_dedicated_key_precedence_and_unset_fallback(monkeypatch, primary):
    calls = []
    def read(SecretId):
        calls.append(SecretId)
        if SecretId == 'primary':
            if primary is None:
                raise ClientError({'Error': {'Code': 'ResourceNotFoundException'}}, 'GetSecretValue')
            return {'SecretString': primary}
        return {'SecretString': 'fallback-key'}
    client = Mock(); client.get_secret_value.side_effect = read
    monkeypatch.setattr('boto3.client', lambda service: client)
    monkeypatch.setenv('CI_CORTEX_OPENAI_SECRET_ARN', 'primary')
    configure()
    import os
    assert os.environ['OPENAI_API_KEY'] == (primary if primary and primary.strip() else 'fallback-key')
    assert calls == (['primary'] if primary and primary.strip() else ['primary', 'fallback'])


def test_permissions_do_not_silently_fall_back(monkeypatch):
    client = Mock()
    client.get_secret_value.side_effect = ClientError({'Error': {'Code': 'AccessDeniedException'}}, 'GetSecretValue')
    monkeypatch.setattr('boto3.client', lambda service: client)
    monkeypatch.setenv('CI_CORTEX_OPENAI_SECRET_ARN', 'primary')
    with pytest.raises(ClientError): configure()
    assert client.get_secret_value.call_count == 1


def test_legacy_configuration_still_works(monkeypatch):
    client = Mock(); client.get_secret_value.return_value = {'SecretString': 'legacy-key'}
    monkeypatch.setattr('boto3.client', lambda service: client)
    configure()
    client.get_secret_value.assert_called_once_with(SecretId='fallback')


@pytest.mark.parametrize('dedicated', ['cortex-key', '', None])
def test_shared_pack_reads_once_and_prefers_dedicated(monkeypatch, dedicated):
    import json, os
    values = {'KJ_CORTEX_DATABASE_URL': 'shared-database', 'OPENAI_API_KEY': 'general-key'}
    if dedicated is not None:
        values['OPENAI_KJ_CORTEX_API_KEY'] = dedicated
    client = Mock(); client.get_secret_value.return_value = {'SecretString': json.dumps(values)}
    monkeypatch.setattr('boto3.client', lambda service: client)
    monkeypatch.setenv('KJ_CORTEX_SECRET_NAME', '/kj/development/all/secrets')
    configure(); configure()
    assert os.environ['KJ_CORTEX_DATABASE_URL'] == 'shared-database'
    assert os.environ['OPENAI_API_KEY'] == (dedicated or 'general-key')
    client.get_secret_value.assert_called_once_with(SecretId='/kj/development/all/secrets')


@pytest.mark.parametrize('raw', ['bad-json', '[]', '{}', '{"KJ_CORTEX_DATABASE_URL": 5}', '{"KJ_CORTEX_DATABASE_URL":"db"}'])
def test_broken_pack_never_falls_back(monkeypatch, raw):
    import os
    client = Mock(); client.get_secret_value.return_value = {'SecretString': raw}
    monkeypatch.setattr('boto3.client', lambda service: client)
    monkeypatch.setenv('KJ_CORTEX_SECRET_NAME', 'shared')
    with pytest.raises((ValueError, RuntimeError)):
        configure()
    assert os.environ['KJ_CORTEX_DATABASE_URL'] == 'test-database'
    client.get_secret_value.assert_called_once_with(SecretId='shared')


def test_shared_pack_permission_error_never_falls_back(monkeypatch):
    client = Mock()
    client.get_secret_value.side_effect = ClientError({'Error': {'Code': 'AccessDeniedException'}}, 'GetSecretValue')
    monkeypatch.setattr('boto3.client', lambda service: client)
    monkeypatch.setenv('KJ_CORTEX_SECRET_NAME', 'shared')
    with pytest.raises(ClientError): configure()
    client.get_secret_value.assert_called_once_with(SecretId='shared')
