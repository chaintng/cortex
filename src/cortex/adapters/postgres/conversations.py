"""Read conversation evidence through the owning database contract."""
from cortex.contracts.evidence import bounded_evidence


class ConversationEvidence:
    def __init__(self, database):
        self.database = database

    def load(self, tenant_id, conversation_id):
        records = self.database.call('evidence', tenant_id, conversation_id)
        return bounded_evidence(records)
