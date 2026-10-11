from unittest.mock import Mock
import pytest
from botocore.exceptions import ClientError
from cortex.adapters.aws.runtime import configure


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    configure.cache_clear()
    for key in ['CI_DATABASE_SECRET_ARN', 'CI_CORTEX_OPENAI_SECRET_ARN', 'OPENAI_CORTEX_API_KEY', 'OPENAI_API_KEY']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('CI_DATABASE_URL', 'test-database')
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
