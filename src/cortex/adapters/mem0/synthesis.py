"""One budgeted model call; Mem0 inference is deliberately not involved."""
import json
import logging
import os
import time
from openai import OpenAI
from cortex.adapters.mem0.engine import BudgetExhausted
from cortex.contracts.synthesis import SYNTHESIS_PROMPT, output_schema

logger = logging.getLogger(__name__)


def synthesize(database, tenant, evidence, enabled, profiles):
    if not evidence:
        return {'memories': [], 'knowledge': [], 'lessons': []}
    if not database.call('reserve_model', tenant):
        raise BudgetExhausted('Tenant synthesis budget exhausted')
    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0, timeout=35)
    started = time.monotonic()
    try:
        result = client.chat.completions.create(
            model='gpt-4.1-mini', temperature=0, max_completion_tokens=6000,
            messages=[{'role': 'system', 'content': SYNTHESIS_PROMPT},
                      {'role': 'user', 'content': json.dumps({'enabled': enabled, 'profiles': profiles, 'evidence': evidence}, ensure_ascii=False)}],
            response_format={'type': 'json_schema', 'json_schema': {'name': 'cortex_synthesis', 'strict': True, 'schema': output_schema(profiles)}})
        choice = result.choices[0]
        if choice.finish_reason != 'stop' or choice.message.refusal or not choice.message.content:
            raise ValueError('Incomplete synthesis response')
        return json.loads(choice.message.content)
    finally:
        logger.info(json.dumps({'metric': 'synthesis_invocation', 'count': 1, 'duration_ms': round((time.monotonic()-started)*1000)}))


def embed_text(content):
    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0, timeout=5)
    started = time.monotonic()
    try:
        return client.embeddings.create(model='text-embedding-3-small', dimensions=1536, input=content).data[0].embedding
    finally:
        logger.info(json.dumps({'metric': 'embedding_invocation', 'count': 1, 'duration_ms': round((time.monotonic()-started)*1000)}))
