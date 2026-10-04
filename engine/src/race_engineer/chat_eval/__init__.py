"""Accuracy checks for the chat (M7 plan section 5): 15 questions asked in two conversations,
each answer checked for tool choice, arguments, grounding and behaviour.

- `questions`: the questions file (engine/evals/chat_questions.toml) and its schema.
- `grounding`: does every number in an answer come from something the model saw?
- `checks`: tool choice, arguments as the tools resolved them, behaviour, one question's result.
- `client`: the API in-process with the scripted chat, or a running API; the run.
- `report`: report/m7_chat_accuracy.md (a run against Claude only) and the JSON record.

`scripts/chat_eval.py` (`make chat-eval`) runs it.
"""
