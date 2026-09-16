"""gitcurator.core — the pure-stdlib testable core.

links (GitHub link extraction), storage (atomic writes + config merge),
note_builder (sanitized frontmatter notes), llm_client (timeout-wrapped
Ollama/chat calls). No PyQt — imported by the GUI, the headless CLI,
the subprocess workers and the CI test gate.
"""
