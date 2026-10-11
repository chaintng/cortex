import hashlib
import json
import tiktoken

def bounded_evidence(records, encoding=None):
    encoding = encoding or tiktoken.get_encoding('cl100k_base')
    selected, tokens = [], 0
    for source in records[:200]:
        content = str(source.get('content') or '')
        if not content.strip():
            continue
        count = len(encoding.encode(content)) + 30
        if tokens + count > 12000:
            continue  # Never truncate evidence while claiming the whole source was supplied.
        selected.append(source)
        tokens += count
    serialized = json.dumps([{k: r[k] for k in ('source_id', 'source_kind', 'source_hash')} for r in selected], sort_keys=True)
    return selected, hashlib.sha256(serialized.encode()).hexdigest(), {'records': len(selected), 'source_tokens': tokens, 'complete_history': False}
