# Cortex architecture

Cortex is a Python service with two deployed adapters and one reserved adapter:

- `apps/worker`: SQS events for extraction, publication and cleanup.
- `apps/api`: IAM-only Lambda requests for search, listing and readiness.
- `apps/mcp`: future MCP adapter, currently documentation only.
- `src/cortex/services`: shared extraction, approved publication and retrieval.
- `src/cortex/contracts`: bounded evidence and extraction configuration.
- `src/cortex/authorization`: backend-derived tenant/customer read scopes.
- `src/cortex/adapters/postgres`: coordination, storage, history and conversation evidence reads.
- `src/cortex/adapters/mem0`: extraction, embeddings and model budget enforcement.
- `src/cortex/adapters/aws`: secret configuration.

Conversation events contain references, not transcripts. The worker claims durable work and asks the conversation adapter for bounded evidence. Shared extraction builds an isolated Mem0 generation. Database contracts activate it only while sources and identity scope remain valid. The service accepts an injected evidence adapter, allowing future source-specific adapters without adding source reads to API or MCP transports.

Database contracts remain in db-contracts; AWS resources in infra; durable event publication in messaging-workers. Existing Lambda/ECR names and request contracts remain stable. KMS owns managed knowledge separately; Cortex supports recall and reviewed knowledge candidates.

Development branch images can publish after tests via the `deploy-dev` PR label, for same-repository PRs only, using the protected development environment. Infra pins the digest and releases aliases separately. No application merge is required. This deployment leaves consumers disabled and does not enable a tenant pilot.
