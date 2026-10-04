"""The chat endpoint's parts: the system prompt (`prompt`), the tools as the model sees them
(`tools`), signed browser-held histories (`session`), the streaming tool-runner loop (`runner`),
token prices (`pricing`), the per-process service the route answers with (`service`) and a
scripted stand-in for Claude used without an API key (`fake`). The route is `api/routes/chat.py`.
"""
