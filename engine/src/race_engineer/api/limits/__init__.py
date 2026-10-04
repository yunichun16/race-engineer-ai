"""The chat's guardrails (plan section 3): per-visitor question limits, the daily spending cap,
the kill switch, request rates and body limits.

- `policy`: the numbers, the records a question leaves (`Admission`, `StoreStatus`) and the
  decision they lead to.
- `identity`: the visitor, a salted daily hash of the client's address (no IP is kept).
- `store`, `memory`, `upstash`: the counters, as Redis commands run in one transaction, in
  memory (local runs, tests) or in Upstash Redis over its REST API (the deployment).
- `guard`: `ChatGuard`, which admits a question before the chat answers it and settles it after.
- `http`: the ASGI middleware for request sizes and request rates.
- `contract`: the store's contract cases, run by the tests and by `limits_admin.py check`.
"""
