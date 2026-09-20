#!/usr/bin/env python3
"""gitcurator.gui.workers.processing — the ProcessingWorker assembly.

The concrete QThread worker: the eight queued signals and ``__init__``
state setup live HERE (signals must be class attributes of the QObject
subclass), while the pipeline phases are mixed in verbatim from the
single-concern mixin modules:

* :class:`~gitcurator.gui.workers._auth.WorkerAuthMixin`   — interactive auth
* :class:`~gitcurator.gui.workers.fetch.SourceFetchMixin`  — telegram/import intake
* :class:`~gitcurator.gui.workers.llm.LlmAnalysisMixin`    — ollama/cloud analysis
* :class:`~gitcurator.gui.workers.assets.BannerAssetMixin` — banner download
* :class:`~gitcurator.gui.workers.scoring.ScoringMixin`    — credibility + notes
* :class:`~gitcurator.gui.workers.notes.NoteGenerationMixin` — index/report/summary
* :class:`~gitcurator.gui.workers.pipeline.PipelineRunMixin` — run()/stop()

No method bodies were changed in this split (md5-verified extraction);
the MRO is collision-free because each method name exists exactly once.
"""

from gitcurator.gui._qt import *  # noqa: F401,F403 — QThread, pyqtSignal, …
from gitcurator.gui.workers._deps import *  # noqa: F401,F403

from gitcurator.gui.workers._auth import WorkerAuthMixin
from gitcurator.gui.workers.fetch import SourceFetchMixin
from gitcurator.gui.workers.llm import LlmAnalysisMixin
from gitcurator.gui.workers.assets import BannerAssetMixin
from gitcurator.gui.workers.scoring import ScoringMixin
from gitcurator.gui.workers.notes import NoteGenerationMixin
from gitcurator.gui.workers.pipeline import PipelineRunMixin

__all__ = ["ProcessingWorker"]


class ProcessingWorker(
    QThread,
    WorkerAuthMixin,
    SourceFetchMixin,
    LlmAnalysisMixin,
    BannerAssetMixin,
    ScoringMixin,
    NoteGenerationMixin,
    PipelineRunMixin,
):
    """ProcessingWorker — the curation pipeline QThread (see module docstring)."""

    progress_updated = pyqtSignal(int, int)
    status_updated = pyqtSignal(str)
    log_message = pyqtSignal(str, str)
    finished_signal = pyqtSignal(bool, str)
    code_requested = pyqtSignal(str)          # "CODE" or "PASSWORD"
    disk_full_signal = pyqtSignal(str)        # path that failed
    llm_failed_signal = pyqtSignal(str)       # repo name
    # v30 — Fix (model persistence): emitted when the user picks a new model
    # from the LLM-failure dialog (or the single-model auto-switch below).
    # Args: (provider, model_name). MainWindow syncs the Settings combo and
    # saves config so the choice survives the batch AND the next launch.
    model_changed = pyqtSignal(str, str)

    def __init__(self, config, mode, range_from=None, range_to=None, offset_start=None, offset_count=None, import_file=None, urls=None, headless=False):
        super().__init__()
        self.config = config
        self.mode = mode
        self.range_from = range_from
        self.range_to = range_to
        self.offset_start = offset_start
        self.offset_count = offset_count
        self.import_file = import_file
        self.urls = urls
        # v30 — Fix (headless hang-bombs): headless mode has NO GUI to answer
        # code_requested / llm_failed_signal / disk_full_signal. Every wait
        # below checks this flag and takes a NON-BLOCKING default instead of
        # stalling the batch (previously: 5-min, 10-min and INFINITE hangs).
        self._headless = bool(headless)
        self.is_running = True
        self.processed = 0
        self._current_position = 0  # tracks ALL URLs (including skips) for progress bar
        self.total = 0
        self._code_event = threading.Event()
        self._code_response = ""
        # LLM failure retry state — blocks worker thread until GUI delivers a decision.
        # Response values: "skip", "retry", "stop", or a model name to retry with.
        self._llm_retry_event = threading.Event()
        self._llm_retry_response = ""
        # Disk-full pause flag — when True, the worker spins waiting for the
        # GUI to clear it after the user frees up disk space.
        self._disk_full_paused = False
        # Vault index for URL-based dedup (ground truth — beats SQLite cache)
        self._vault_index = None
        # Batch undo snapshot path (v22 — Feature 6: Batch Undo)
        self._batch_snapshot_path = ""
        # v23 — No Link Left Behind: tracks every URL through a 5-phase
        # pipeline. The manifest file is the source of truth.
        self.link_tracker = None
        # Flag set by MainWindow when the URLs come from the bot queue (so
        # the link tracker can record the correct source).
        self._bot_source = False
        # v25 pre-flight: banner download throttle counter — incremented on
        # every _download_banner() call so we can pause periodically and
        # avoid opengraph.githubassets.com 429s during large batches.
        self._banner_count = 0
        # v25 pre-flight: total duplicate URLs removed during intake (raw vs
        # unique). Surfaced in the final report.
        self._intake_duplicates = 0
        # v25 pre-flight: total raw URLs seen during intake (before dedup).
        # Paired with _intake_duplicates for the "N unique from M total"
        # display in the final report.
        self._raw_url_count = 0
