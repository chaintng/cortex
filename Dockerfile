FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM public.ecr.aws/lambda/python:3.12
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt && uv pip install --system --target ${LAMBDA_TASK_ROOT} -r /tmp/requirements.txt && rm /tmp/requirements.txt
ENV TIKTOKEN_CACHE_DIR=/var/task/tiktoken-cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"
COPY src/cortex ./cortex
COPY apps ./apps
ENV MEM0_TELEMETRY=false MEM0_DIR=/tmp/kj-mem0
CMD ["apps.lambda_handler.lambda_handler"]
