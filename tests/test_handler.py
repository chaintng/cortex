from uuid import uuid4
from unittest.mock import Mock
import pytest
from apps.worker.handler import handle as worker_handle
from apps.api.handler import handle as api_handle
from cortex.services.memory import retrieve


def test_duplicate_intake_is_acknowledged_only_after_durable_write(monkeypatch):
    monkeypatch.setenv('CI_PIPELINE', 'knowledge')
    database = Mock()
    database.call.side_effect = ['intake', 'duplicate']
    event_id = str(uuid4())
    result = worker_handle(database, {'Records': [{'messageId': 'message', 'body': '{"event_id":"'+event_id+'"}'}]})
    assert result == {'batchItemFailures': []}
    database.call.assert_any_call('intake', event_id, 'knowledge')


def test_failed_durable_transition_keeps_queue_message(monkeypatch):
    monkeypatch.setenv('CI_PIPELINE', 'memory')
    database = Mock()
    database.call.side_effect = ['intake', RuntimeError('database unavailable')]
    result = worker_handle(database, {'Records': [{'messageId': 'retry', 'body': '{"event_id":"'+str(uuid4())+'"}'}]})
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
    monkeypatch.setattr('cortex.services.memory.SupabaseMemory', model)
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'ready': True}
    assert api_handle(database, {'operation': 'health'}) == {'status': 'ready', 'storage': 'postgresql', 'model_called': False}
    database.call.assert_not_called()
    model.assert_not_called()


def test_readiness_fails_when_runtime_role_is_not_restricted():
    from unittest.mock import MagicMock
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'ready': False}
    with pytest.raises(RuntimeError, match='Restricted database role'):
        api_handle(database, {'operation': 'health'})


@pytest.mark.parametrize('mode,event', [
    ('retrieval', {'Records': []}),
    ('intake', {'operation': 'search'}),
    ('knowledge-intake', {'operation': 'health'}),
    ('unexpected', {'Records': []}),
])
def test_transport_rejects_wrong_trigger_before_credentials_or_database(monkeypatch, mode, event):
    from apps.lambda_handler import lambda_handler
    configure = Mock(side_effect=AssertionError('Unexpected credentials read'))
    monkeypatch.setattr('apps.lambda_handler.configure', configure)
    monkeypatch.setenv('CI_MODE', mode)
    with pytest.raises(ValueError):
        lambda_handler(event, None)
    configure.assert_not_called()




@pytest.mark.parametrize('mode', ['intake', 'knowledge-intake', 'extraction', 'maintenance', 'retrieval'])
def test_each_deployed_mode_routes_to_its_adapter_and_closes_database(monkeypatch, mode):
    from apps.lambda_handler import lambda_handler
    database = Mock()
    api, worker = Mock(return_value='api'), Mock(return_value='worker')
    monkeypatch.setenv('CI_MODE', mode)
    monkeypatch.setattr('apps.lambda_handler.configure', Mock())
    monkeypatch.setattr('apps.lambda_handler.Database', Mock(return_value=database))
    monkeypatch.setattr('apps.lambda_handler.api_handle', api)
    monkeypatch.setattr('apps.lambda_handler.worker_handle', worker)
    event = {'operation': 'health'} if mode == 'retrieval' else {'Records': []}
    assert lambda_handler(event, None) == ('api' if mode == 'retrieval' else 'worker')
    (api if mode == 'retrieval' else worker).assert_called_once_with(database, event)
    (worker if mode == 'retrieval' else api).assert_not_called()
    database.close.assert_called_once()
