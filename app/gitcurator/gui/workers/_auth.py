#!/usr/bin/env python3
"""gitcurator.gui.workers._auth — the interactive-auth mixin.

Telegram login-code / LLM-failure arbitration between the worker thread
and the GUI (code_requested signal -> provide_code, the v30 non-blocking
headless defaults). Verbatim methods of the original ProcessingWorker.

"""

from gitcurator.gui._qt import *  # noqa: F401,F403
from gitcurator.gui.workers._deps import *  # noqa: F401,F403
__all__ = ["WorkerAuthMixin"]


class WorkerAuthMixin:
    """WorkerAuthMixin — see module docstring (methods are verbatim moves)."""


    def provide_code(self, code: str):
        """Called from the GUI thread to deliver the login code/password."""
        self._code_response = code
        self._code_event.set()

    def request_code(self, prompt_type: str = "CODE") -> str:
        """Called from the worker thread. Emits code_requested, then blocks
        until the GUI thread calls provide_code().

        v30 — Fix (headless hang-bomb): in headless mode nobody can ever
        call provide_code() — the old code still waited 5 minutes before
        giving up. Now it fails fast with '' (the Telethon fetcher treats
        an empty code as an auth failure and aborts the fetch cleanly)."""
        if self._headless:
            self.log_message.emit(
                "⏭️ Telegram login code requested in headless mode — no GUI to answer. "
                "Run the GUI once to log in (session is then reused), or use bot-queue mode.",
                "warning"
            )
            return ""
        self._code_event.clear()
        self._code_response = ""
        self.code_requested.emit(prompt_type)
        timed_out = not self._code_event.wait(timeout=300)
        if timed_out:
            self.log_message.emit("⏰ Auth code input timed out (5 minutes)", "warning")
        return self._code_response

    def resolve_llm_failure(self, response: str):
        """Called from GUI thread to deliver the user's LLM failure decision.
        Response values: 'skip', 'retry', 'stop', or a model name to retry with."""
        self._llm_retry_response = response
        self._llm_retry_event.set()

    def _wait_for_llm_decision(self, repo_name: str, attempt: int) -> str:
        """Called from worker thread. Blocks until GUI delivers a decision.
        Returns: 'skip', 'retry', 'stop', or a model name to retry with.

        v30 — Fix (headless hang-bomb): headless mode has no dialog to
        answer the signal — the old code burned a 10-MINUTE timeout per
        failed repo. Now it skips immediately with fallback values (same
        outcome as pressing 'Skip' in the GUI: the note is still written
        with placeholder content, the batch continues)."""
        if self._headless:
            self.log_message.emit(
                f"⏭️ Headless mode: LLM failed for '{repo_name}' — using fallback "
                "values for the note (same as the GUI 'Skip' button) and continuing.",
                "warning"
            )
            return "skip"
        self._llm_retry_event.clear()
        self._llm_retry_response = ""
        self.llm_failed_signal.emit(repo_name)  # signal GUI to show dialog
        self._llm_retry_event.wait(timeout=600)  # 10 minute timeout
        return self._llm_retry_response or "skip"

    def _apply_model_choice(self, new_model: str) -> None:
        """v30 — Fix (model persistence): apply a user/AI-selected model to
        self.config IN PLACE (never rebind — other threads hold this dict)
        and notify the GUI so it updates the Settings combo + saves config.

        This is THE fix for 'the system asks for the model on every link':
        previously the dialog's model choice was used for a single retry of
        the current repo only; the batch kept using the stale model for
        every subsequent repo, so the modal reappeared for each one."""
        if not new_model:
            return
        provider = self.config.get('llm_provider', 'ollama')
        if provider == 'cloud':
            self.config['cloud_model'] = new_model
        else:
            # Mutate the nested dict in place — self.config must never be
            # rebound (the GUI + save_config hold references to it).
            ollama_cfg = self.config.get('ollama')
            if not isinstance(ollama_cfg, dict):
                ollama_cfg = {}
                self.config['ollama'] = ollama_cfg
            ollama_cfg['model'] = new_model
        try:
            self.model_changed.emit(provider, new_model)
        except Exception:
            pass  # signal delivery is best-effort
