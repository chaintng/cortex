from uuid import UUID


def read_scope(database, request):
    tenant = str(UUID(request['tenant_id']))
    conversation = request['conversation_id']
    actor = str(UUID(request['actor_user_id'])) if request.get('actor_user_id') else None
    scope = database.call('read_scope', tenant, conversation, request['operation'] == 'knowledge_search', actor)
    return tenant, conversation, actor, scope
