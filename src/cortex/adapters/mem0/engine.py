import os
import json
import logging
import time
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
os.environ['MEM0_TELEMETRY'] = 'false'
os.environ['MEM0_DIR'] = '/tmp/kj-mem0'

from mem0 import Memory
from mem0.configs.base import MemoryConfig
from mem0.utils.factory import EmbedderFactory, LlmFactory
from openai import OpenAI
from cortex.adapters.postgres.storage import PostgresVectors, PostgresHistory


class BudgetExhausted(Exception):
    pass


class BudgetedModel:
    def __init__(self, model, database, tenant):
        self.model, self.database, self.tenant = model, database, tenant
        self.failure = None

    def generate_response(self, *args, **kwargs):
        try:
            if not self.database.call('reserve_model', self.tenant):
                logger.info(json.dumps({'metric': 'model_budget_deferred', 'count': 1}))
                raise BudgetExhausted('Tenant model budget exhausted')
            started = time.monotonic()
            try:
                response = self.model.generate_response(*args, **kwargs)
            finally:
                logger.info(json.dumps({'metric': 'model_invocation', 'count': 1, 'duration_ms': round((time.monotonic()-started)*1000)}))
            parsed = json.loads(response)
            if not isinstance(parsed, dict) or not any(isinstance(parsed.get(k), list) for k in ('facts', 'memory')):
                raise ValueError('Invalid Mem0 model response')
            return response
        except Exception as error:
            self.failure = error
            raise



class MeasuredEmbedding:
    def __init__(self, model):
        self.model, self.failure = model, None

    def __getattr__(self, name):
        return getattr(self.model, name)

    def embed(self, *args, **kwargs):
        started = time.monotonic()
        try:
            return self.model.embed(*args, **kwargs)
        except Exception as error:
            self.failure = error
            raise
        finally:
            logger.info(json.dumps({'metric': 'embedding_invocation', 'count': 1, 'duration_ms': round((time.monotonic()-started)*1000)}))


class SupabaseMemory(Memory):
    """Explicit dependency assembly for mem0ai==1.0.11, without its SQLite/DDL initializer.

    Inherited Mem0 add/search algorithms remain authoritative. Compatibility tests
    must pass before changing the pinned release.
    """
    def __init__(self, database, tenant, generations, writable_generation=None, instructions=None, fake_llm=None, fake_embedder=None):
        self.config = MemoryConfig(llm={'provider': 'openai', 'config': {'model': 'gpt-4.1-mini', 'temperature': 0, 'max_tokens': 3000}},
                                   embedder={'provider': 'openai', 'config': {'model': 'text-embedding-3-small', 'embedding_dims': 1536}},
                                   custom_fact_extraction_prompt=instructions)
        self.custom_fact_extraction_prompt = self.config.custom_fact_extraction_prompt
        self.custom_update_memory_prompt = self.config.custom_update_memory_prompt
        self.vector_store = PostgresVectors(database, tenant, generations, writable_generation)
        self.embedding_model = fake_embedder or EmbedderFactory.create(self.config.embedder.provider, self.config.embedder.config, self.config.vector_store.config)
        self.llm = fake_llm or LlmFactory.create(self.config.llm.provider, self.config.llm.config)
        if not fake_llm:
            self.llm.client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0, timeout=35)
            self.llm = BudgetedModel(self.llm, database, tenant)
        if not fake_embedder:
            self.embedding_model.client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0, timeout=5)
        self.embedding_model = MeasuredEmbedding(self.embedding_model)
        self.db = PostgresHistory(database, tenant, writable_generation or generations[0])
        self.collection_name, self.api_version = 'vectors', 'v1.1'
        self.reranker, self.graph, self.enable_graph = None, None, False

    def assert_success(self):
        # Mem0 catches some update-loop failures; they must never activate partial results.
        if getattr(self.llm, 'failure', None):
            raise self.llm.failure
        if self.embedding_model.failure:
            raise self.embedding_model.failure
        if self.vector_store.db.failure:
            raise self.vector_store.db.failure
