---
name: technical-spec
description: Use to transform a functional requirement or feature spec into a detailed technical specification aligned with the existing repository architecture.
disable-model-invocation: true
---

# Technical Specification

Before designing, inspect the existing implementation and conventions.

Produce:

1. Summary
2. Existing architecture/context
3. Proposed architecture
4. Component changes
5. Data flow
6. API changes
7. Database/schema changes
8. Queue/job changes
9. Filesystem/storage changes
10. Model/provider changes
11. CPU/GPU considerations
12. Error handling and retries
13. Idempotency/resumability
14. Observability
15. Security
16. Performance/resource considerations
17. Test plan
18. Migration plan
19. Rollback plan
20. Alternatives considered
21. Implementation sequence

Prefer the smallest architecture change that cleanly supports the feature.
