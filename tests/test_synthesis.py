from unittest.mock import Mock
import pytest
from cortex.contracts.synthesis import validate_outputs, config_hash
from cortex.services.memory import extract, publish
from cortex.services.publication import text_chunks

ENABLED = {'memory': True, 'knowledge': True, 'lessons': True}
PROFILES = [{'skill_code': 'kj_sales', 'runtime_skill': 'sales', 'path_key': 'sales_foundations', 'profile_version': 1, 'lesson_keys': ['discovery']}]
EVIDENCE = [{'source_id': 'source', 'source_kind': 'message', 'source_hash': 'hash', 'role': 'user', 'content': 'Prefer Thai'}]
OUTPUTS = {
    'memories': [{'kind': 'fact', 'content': 'Prefers Thai', 'source_ids': ['source']}],
    'knowledge': [{'content': 'Delivery is available', 'source_ids': ['source']}],
    'lessons': [{'content': 'Ask what the customer needs', 'source_ids': ['source'], 'skill_code': 'kj_sales', 'path_key': 'sales_foundations', 'lesson_key': 'discovery', 'profile_version': 1}],
}


def database_for(outputs=None, memory_done=False):
    db = Mock()
    values = {
        'claim': {'status': 'claimed', 'lease_token': 'lease', 'tenant_id': 'tenant', 'conversation_id': 'chat', 'revision': 7, 'outputs_enabled': ENABLED, 'profiles': PROFILES},
        'prepare_synthesis': {'status': 'build', 'run_id': 'run', 'outputs': outputs, 'memory_done': memory_done},
        'save_synthesis': OUTPUTS,
        'prepare_memory': {'status': 'build', 'generation_id': 'generation'},
        'finish_synthesis': 'ready', 'fail': None,
    }
    db.call.side_effect = lambda name, *args: values[name]
    return db


def evidence_reader():
    evidence = Mock()
    evidence.load.return_value = (EVIDENCE, 'digest', {'records': 1})
    return evidence


def test_one_synthesis_populates_all_outputs_and_mem0_does_not_extract_again(monkeypatch):
    database = database_for()
    synthesis = Mock(return_value=OUTPUTS)
    memory = Mock(spec=["add", "assert_success"])
    monkeypatch.setattr('cortex.services.memory.SupabaseMemory', Mock(return_value=memory))
    assert extract(database, 'event', evidence_reader(), synthesis) == 'ready'
    synthesis.assert_called_once()
    memory.add.assert_called_once_with('FACT: Prefers Thai', user_id='tenant', run_id='generation', metadata={'conversation_id': 'chat', 'source_ids': ['source']}, infer=False)
    names = [call.args[0] for call in database.call.call_args_list]
    assert names.index('save_synthesis') < names.index('prepare_memory') < names.index('finish_synthesis')


def test_embedding_failure_preserves_synthesis_and_retry_does_not_call_model(monkeypatch):
    database = database_for()
    memory = Mock(spec=["add", "assert_success"])
    memory.add.side_effect = RuntimeError('embedding unavailable')
    monkeypatch.setattr('cortex.services.memory.SupabaseMemory', Mock(return_value=memory))
    with pytest.raises(RuntimeError): extract(database, 'event', evidence_reader(), Mock(return_value=OUTPUTS))
    database.call.assert_any_call('fail', 'event', 'lease', False)
    memory.add.side_effect = None
    synthesis = Mock(side_effect=AssertionError('No repeated synthesis'))
    assert extract(database_for(OUTPUTS), 'event', evidence_reader(), synthesis) == 'ready'
    synthesis.assert_not_called()


def test_unchanged_evidence_skips_model_and_embeddings(monkeypatch):
    db = Mock()
    db.call.side_effect = [database_for().call('claim'), {'status': 'unchanged'}]
    model = Mock(side_effect=AssertionError('No inference'))
    assert extract(db, 'event', evidence_reader(), model) == 'unchanged'
    model.assert_not_called()


@pytest.mark.parametrize('change', ['source', 'skill', 'disabled', 'kind', 'oversized'])
def test_invalid_outputs_are_rejected(change):
    import copy
    output, enabled = copy.deepcopy(OUTPUTS), ENABLED.copy()
    if change == 'source': output['memories'][0]['source_ids'] = ['invented']
    if change == 'skill': output['lessons'][0]['skill_code'] = 'other'
    if change == 'disabled': enabled['lessons'] = False
    if change == 'kind': output['memories'][0]['kind'] = 'policy'
    if change == 'oversized': output['knowledge'][0]['content'] = 'x' * 4001
    with pytest.raises(ValueError): validate_outputs(output, EVIDENCE, enabled, PROFILES)


def test_profile_and_enablement_changes_change_config_hash():
    assert config_hash(ENABLED, PROFILES) != config_hash({**ENABLED, 'lessons': False}, PROFILES)
    assert config_hash(ENABLED, PROFILES) != config_hash(ENABLED, [{**PROFILES[0], 'profile_version': 2}])


def test_publication_embeddings_preserve_exact_thai_text():
    import tiktoken
    codec = tiktoken.get_encoding('o200k_base')
    content = 'เรียนรู้จากบทสนทนา 🌱\n' * 150
    chunks = list(text_chunks(content, codec))
    assert ''.join(chunks) == content
    assert all(len(codec.encode(chunk)) <= 800 for chunk in chunks)
    db = Mock()
    db.call.side_effect = [{'status': 'build', 'publication_id': 'publication', 'content': content}, 'active']
    assert publish(db, 'event', lambda text: [0.0] * 1536) == 'active'
    assert db.call.call_args_list[-1].args[0] == 'finish_kms_publication'


def test_publication_failure_records_failure_without_model_call():
    db = Mock()
    db.call.side_effect = [{'status': 'build', 'publication_id': 'publication', 'content': 'Reviewed text'}, None]
    with pytest.raises(RuntimeError): publish(db, 'event', Mock(side_effect=RuntimeError('embedding failed')))
    db.call.assert_any_call('fail_publication', 'event')
