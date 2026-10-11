from uuid import uuid4
from unittest.mock import Mock
import pytest
from cortex.handler import handle, retrieve


def test_duplicate_intake_is_acknowledged_only_after_durable_write(monkeypatch):
    monkeypatch.setenv('CI_PIPELINE', 'knowledge')
    database = Mock()
    database.call.side_effect = ['intake', 'duplicate']
    event_id = str(uuid4())
    result = handle(database, {'Records': [{'messageId': 'message', 'body': '{"event_id":"'+event_id+'"}'}]})
    assert result == {'batchItemFailures': []}
    database.call.assert_any_call('intake', event_id, 'knowledge')


def test_failed_durable_transition_keeps_queue_message(monkeypatch):
    monkeypatch.setenv('CI_PIPELINE', 'memory')
    database = Mock()
    database.call.side_effect = ['intake', RuntimeError('database unavailable')]
    result = handle(database, {'Records': [{'messageId': 'retry', 'body': '{"event_id":"'+str(uuid4())+'"}'}]})
    assert result == {'batchItemFailures': [{'itemIdentifier': 'retry'}]}


def test_request_filters_do_not_set_mem0_scope():
    database = Mock()
    database.call.return_value = {'generations': []}
    tenant = str(uuid4())
    assert retrieve(database, {'operation': 'search', 'tenant_id': tenant, 'conversation_id': 'chat', 'query': 'hello', 'filters': {'tenant': 'other'}}) == {'items': []}
    database.call.assert_called_once_with('read_scope', tenant, 'chat', False, None)


def test_readiness_checks_storage_without_model_or_customer_reads(monkeypatch):
    from unittest.mock import MagicMock
    model = Mock(side_effect=AssertionError('Readiness must not initialize Mem0'))
    monkeypatch.setattr('cortex.handler.SupabaseMemory', model)
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'ready': True}
    assert handle(database, {'operation': 'health'}) == {'status': 'ready', 'storage': 'postgresql', 'model_called': False}
    database.call.assert_not_called()
    model.assert_not_called()


def test_readiness_fails_when_runtime_role_is_not_restricted():
    from unittest.mock import MagicMock
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'ready': False}
    with pytest.raises(RuntimeError, match='Restricted database role'):
        handle(database, {'operation': 'health'})
