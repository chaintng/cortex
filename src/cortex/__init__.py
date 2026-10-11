import os
os.environ["MEM0_TELEMETRY"]="false"
os.environ["MEM0_DIR"]="/tmp/kj-mem0"

# The pinned Mem0 release logs extracted text/actions on normal and error paths.
# Keep service metrics, but suppress those upstream transcript-bearing loggers.
import logging
for namespace in ('mem0.memory.main', 'mem0.memory.utils', 'psycopg.pool'):
    logging.getLogger(namespace).disabled = True
