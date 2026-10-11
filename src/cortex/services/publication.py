"""Index exact approved text into KMS. No generative calls during publication."""
import tiktoken
from psycopg.types.json import Jsonb
from cortex.adapters.postgres.storage import vector_literal


def text_chunks(content, codec, limit=800):
    while content:
        low, high = 1, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            if len(codec.encode(content[:middle])) <= limit:
                low = middle
            else:
                high = middle - 1
        yield content[:low]
        content = content[low:]


def index_publication(database, event_id, embedder):
    prepared = database.call('prepare_kms_publication', event_id)
    if prepared['status'] != 'build':
        return prepared['status']
    try:
        codec = tiktoken.get_encoding('o200k_base')
        chunks = [{'index': index, 'content': text, 'tokens': len(codec.encode(text)), 'embedding': vector_literal(embedder(text))}
                  for index, text in enumerate(text_chunks(prepared['content'], codec))]
        return database.call('finish_kms_publication', event_id, prepared['publication_id'], Jsonb(chunks))
    except Exception:
        database.call('fail_publication', event_id)
        raise
