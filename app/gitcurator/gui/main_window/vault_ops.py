#!/usr/bin/env python3
"""gitcurator.gui.main_window.vault_ops — the vault operations mixin.

Dashboard statistics scan (frontmatter-only), vault verify,
recategorize, and batch-undo via snapshot restore (verbatim methods of
the original MainWindow).
"""

import os, re
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["VaultOpsMixin"]


class VaultOpsMixin:
    """VaultOpsMixin — see module docstring (methods are verbatim moves)."""


    def update_dashboard(self):
        """Scan the vault and display statistics in the Dashboard tab.

        Only reads the first ~1KB of each .md file (frontmatter) so it stays
        fast even on large vaults.
        """
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self.dashboard_text.setPlainText("No vault selected.")
            return

        self.log_message("📊 Refreshing dashboard...", "info")

        stats = {
            'total_notes': 0,
            'categories': {},
            'languages': {},
            'avg_credibility': 0.0,
            'credibility_scores': [],
            'banners': 0,
            'review_queue': 0,
        }

        for root, dirs, files in os.walk(vault):
            for fname in files:
                if fname.endswith('.md'):
                    fpath = os.path.join(root, fname)
                    stats['total_notes'] += 1

                    # Check if in _review folder
                    if '_review' in root:
                        stats['review_queue'] += 1

                    # Read frontmatter (only the first 1KB for speed)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            content = f.read(1000)

                        cat_match = re.search(r'category:\s*(.+)', content)
                        if cat_match:
                            cat = cat_match.group(1).strip()
                            stats['categories'][cat] = stats['categories'].get(cat, 0) + 1

                        cred_match = re.search(r'credibility_score:\s*([\d.]+)', content)
                        if cred_match:
                            try:
                                cred = float(cred_match.group(1))
                                stats['credibility_scores'].append(cred)
                            except ValueError:
                                pass

                        lang_match = re.search(r'primary_language:\s*(.+)', content)
                        if lang_match:
                            lang = lang_match.group(1).strip()
                            stats['languages'][lang] = stats['languages'].get(lang, 0) + 1
                    except Exception:
                        pass

                elif fname.endswith('.png') and 'banner' in fname:
                    stats['banners'] += 1

        # Calculate average credibility
        if stats['credibility_scores']:
            stats['avg_credibility'] = sum(stats['credibility_scores']) / len(stats['credibility_scores'])

        # Build dashboard text
        lines = []
        lines.append("=" * 50)
        lines.append("📊 VAULT DASHBOARD")
        lines.append("=" * 50)
        lines.append(f"Vault: {vault}")
        lines.append(f"Total notes: {stats['total_notes']}")
        lines.append(f"Banners downloaded: {stats['banners']}")
        lines.append(f"Review queue: {stats['review_queue']}")
        lines.append(f"Average credibility: {stats['avg_credibility']:.1f}/100")
        lines.append("")
        lines.append("📁 NOTES BY CATEGORY:")
        lines.append("-" * 50)
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1]):
            lines.append(f"  {cat:40s} {count:3d}")
        lines.append("")
        lines.append("💻 NOTES BY LANGUAGE:")
        lines.append("-" * 50)
        for lang, count in sorted(stats['languages'].items(), key=lambda x: -x[1]):
            lines.append(f"  {lang:40s} {count:3d}")
        lines.append("")
        lines.append("=" * 50)

        self.dashboard_text.setPlainText('\n'.join(lines))
        self.log_message(
            f"✅ Dashboard updated: {stats['total_notes']} notes, "
            f"{len(stats['categories'])} categories",
            "success"
        )

    def verify_vault(self):
        """v23 — Phase 3 manual verification — read the manifest, check every
        GitHub link has a non-empty note file on disk, and every non-GitHub
        link is in the inbox table. Shows a detailed report in the dashboard
        text area. Offers to retry any failed links via the existing retry
        flow (cache.add_failed).

        v26 — Fix 2: the entire method is wrapped in a try/except so a crash
        anywhere (missing manifest file, malformed JSON, unexpected attribute
        on the tracker) is reported to the user instead of taking down the
        whole app. The old ``QMessageBox.question`` retry prompt has been
        replaced with a theme-aware custom dialog (no native modal that can
        hide behind the main window on some WMs) AND the failed URLs are
        added to the retry queue unconditionally — the user no longer has to
        click 'Yes' to enqueue them."""
        try:
            vault = self.vault_combo.currentText()
            if not vault or not os.path.isdir(vault):
                try:
                    self.dashboard_text.setPlainText("No vault selected.")
                except Exception:
                    pass
                return

            tracker = LinkTracker(vault)
            prev = tracker.load_previous_manifest()
            if not prev:
                try:
                    self.dashboard_text.setPlainText(
                        "No previous manifest found in this vault.\n"
                        "Process a batch first — the manifest is created at the start of every batch."
                    )
                except Exception:
                    pass
                self.log_message("ℹ️ No manifest to verify — process a batch first.", "info")
                return

            # Load the previous manifest into the tracker so verify() can
            # re-check every link against the current state of the vault.
            tracker.manifest = prev
            # v29 fix: log_signal must be a Qt signal (with .emit()), not a method.
            # self.log_message is a method in MainWindow, so pass None.
            report = tracker.verify(log_signal=None)

            # Log the summary manually
            self.log_message(f"🔍 Verify: {report.get('github_processed', 0)} processed, {report.get('github_failed', 0)} failed", "info")

            # Render a detailed report in the dashboard text area
            lines = []
            lines.append("=" * 60)
            lines.append("🔍 VAULT VERIFICATION REPORT")
            lines.append("=" * 60)
            lines.append(f"Batch:    {prev.get('batch_id', 'unknown')}")
            lines.append(f"Source:   {prev.get('source', 'unknown')}")
            lines.append(f"Created:  {prev.get('created_at', 'unknown')}")
            lines.append(f"Total links: {report['total']}")
            lines.append("")
            lines.append(f"✅ GitHub processed:    {report['github_processed']}")
            lines.append(f"⏭️ GitHub skipped:      {report['github_skipped']}  (dedup — already in vault)")
            lines.append(f"❌ GitHub failed:       {report['github_failed']}")
            lines.append(f"✅ Non-GitHub recorded: {report['non_github_recorded']}")
            lines.append(f"❌ Non-GitHub failed:   {report['non_github_failed']}")
            lines.append("")

            if report["verification_passed"]:
                lines.append("🎉 ALL LINKS VERIFIED — NO DATA LOSS!")
            else:
                lines.append(f"⚠️ {len(report['failed_links'])} link(s) need retry:")
                lines.append("")
                for fl in report["failed_links"]:
                    lines.append(f"  ❌ {fl.get('url', '?')}")
                    lines.append(f"     Type:   {fl.get('type', '?')}")
                    lines.append(f"     Status: {fl.get('status', '?')}")
                    lines.append(f"     Error:  {fl.get('error', 'unknown')}")
                    lines.append("")

            lines.append("=" * 60)
            try:
                self.dashboard_text.setPlainText('\n'.join(lines))
            except Exception:
                pass

            # v26 — Fix 2: failed URLs are enqueued directly (no Yes/No prompt
            # that could crash if the parent window is being torn down) and
            # the user is shown a custom theme-aware dialog telling them what
            # happened. The retry queue is the canonical source of "links to
            # reprocess" so this is safe even if the user clicks the Verify
            # Vault button many times.
            failed_urls = [fl.get('url') for fl in report['failed_links'] if fl.get('url')]
            if failed_urls:
                self._show_custom_message_box(
                    "Failed Links Found",
                    f"{len(failed_urls)} link(s) failed verification.\n\n"
                    "They have been added to the retry queue.\n"
                    "Click '🔄 Retry Failed' in the Bot tab to reprocess them.",
                    success=False
                )
                # Add to retry queue directly (no question dialog).
                try:
                    cache = CacheDB()
                    for u in failed_urls:
                        try:
                            cache.add_failed(u, "failed Phase 3 verification")
                        except Exception:
                            pass
                    cache.close()
                    self.log_message(
                        f"📥 Added {len(failed_urls)} failed link(s) to the retry queue. "
                        f"Click '🔄 Retry Failed' in the Bot tab to reprocess them.",
                        "info"
                    )
                except Exception as e:
                    self.log_message(f"❌ Failed to enqueue retries: {e}", "error")
        except Exception as e:
            # v26 — Fix 2: never let a Verify Vault crash take down the app.
            import traceback
            error_msg = f"Verify Vault crashed: {e}\n\n{traceback.format_exc()}"
            try:
                self.dashboard_text.setPlainText(error_msg)
            except Exception:
                pass
            self.log_message(f"❌ Verify Vault crashed: {e}", "error")

    def recategorize_notes(self):
        """Show a table of notes and let the user bulk-reassign categories.

        For each row the user picks a new category from a dropdown. On
        'Apply Changes', notes whose category changed are rewritten with the
        new `category:` frontmatter value and moved to the corresponding
        category folder. The old file is removed only if the new path differs.
        """
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid vault first.", success=False)
            return

        # Collect all notes with their current categories
        notes = []
        for root, dirs, files in os.walk(vault):
            for fname in files:
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read(1000)
                    cat_match = re.search(r'category:\s*(.+)', content)
                    cat = cat_match.group(1).strip() if cat_match else "Unknown"
                    notes.append({'path': fpath, 'name': fname, 'category': cat})
                except Exception:
                    pass

        if not notes:
            self._show_custom_message_box("Recategorize", "No notes found in vault.", success=True)
            return

        # Create dialog with table
        dialog = QDialog(self)
        dialog.setWindowTitle(f"📁 Recategorize Notes ({len(notes)} notes)")
        
        dialog.setMinimumWidth(800)
        dialog.setMinimumHeight(500)

        layout = QVBoxLayout(dialog)

        # Table
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QComboBox, QHeaderView
        table = QTableWidget(len(notes), 3)
        table.setHorizontalHeaderLabels(["Note", "Current Category", "New Category"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for i, note in enumerate(notes):
            table.setItem(i, 0, QTableWidgetItem(note['name']))
            table.setItem(i, 1, QTableWidgetItem(note['category']))
            combo = QComboBox()
            combo.addItems(CATEGORY_KEYS)
            idx = combo.findText(note['category'])
            if idx >= 0:
                combo.setCurrentIndex(idx)
            table.setCellWidget(i, 2, combo)

        layout.addWidget(table)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        apply_btn = QPushButton("✓ Apply Changes")
        apply_btn.setStyleSheet(self._btn_style(COLORS['cta'], COLORS['cta_hover'], text=COLORS['cta_text']))
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "padding: 8px 20px; border: 1px solid #ccc; border-radius: 5px;"
        )
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

        def _apply():
            moved = 0
            for i, note in enumerate(notes):
                combo = table.cellWidget(i, 2)
                new_cat = combo.currentText()
                if new_cat != note['category']:
                    try:
                        with open(note['path'], 'r', encoding='utf-8') as f:
                            full_content = f.read()
                        new_content = re.sub(
                            r'category:\s*.+',
                            f'category: {new_cat}',
                            full_content
                        )
                        # Write to new location
                        new_folder = os.path.join(
                            vault, CATEGORY_FOLDERS.get(new_cat, "Uncategorized")
                        )
                        os.makedirs(new_folder, exist_ok=True)
                        new_path = os.path.join(new_folder, note['name'])

                        with open(new_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)

                        # Delete old file if different location
                        if os.path.abspath(new_path) != os.path.abspath(note['path']):
                            os.remove(note['path'])

                        # Update SQLite cache so dedup check finds the note at the new path
                        cache = None
                        try:
                            cache = CacheDB()
                            # Find the repo_id by matching the old note_path
                            cache.cursor.execute(
                                "SELECT repo_id FROM processed_repos WHERE note_path = ?",
                                (note['path'],)
                            )
                            row = cache.cursor.fetchone()
                            if row:
                                repo_id = row[0]
                                cache.cursor.execute(
                                    "UPDATE processed_repos SET note_path = ?, category = ? WHERE repo_id = ?",
                                    (new_path, new_cat, repo_id)
                                )
                                cache.conn.commit()
                        except Exception as cache_err:
                            self.log_message(
                                f"   ⚠️ Cache update failed for {note['name']}: {cache_err}",
                                "warning"
                            )
                        finally:
                            if cache:
                                try:
                                    cache.close()
                                except Exception:
                                    pass

                        moved += 1
                    except Exception as e:
                        self.log_message(
                            f"Failed to recategorize {note['name']}: {e}", "error"
                        )

            self.log_message(f"📁 Recategorized {moved} notes", "success")
            dialog.accept()
            if moved > 0:
                self.update_dashboard()

        apply_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dialog.reject)

        self._animate_dialog(dialog)
        dialog.exec()

    def undo_last_batch(self):
        """v22 Feature 6: Delete the .md files written by the most recent
        processing batch. The list is stored at `<vault>/_undo_last_batch.txt`
        by ProcessingWorker.run() after a batch completes.

        Asks for confirmation, deletes each file (best-effort), and removes
        the corresponding entries from the SQLite cache + vault index."""
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        undo_path = os.path.join(vault, '_undo_last_batch.txt')
        if not os.path.isfile(undo_path):
            self._show_custom_message_box("No Batch to Undo", "No _undo_last_batch.txt file found in the vault.\n"
                                    "Nothing to undo.", success=True)
            return

        # Read the list of files (one path per line)
        try:
            with open(undo_path, 'r', encoding='utf-8') as uf:
                file_list = [line.strip() for line in uf if line.strip()]
        except Exception as e:
            self._show_custom_message_box("Error", f"Failed to read undo list: {e}", success=False)
            return

        if not file_list:
            self._show_custom_message_box("No Batch to Undo", "The undo list is empty. Nothing to undo.", success=True)
            return

        existing = [f for f in file_list if os.path.isfile(f)]
        if not existing:
            self._show_custom_message_box("Nothing to Undo", "All files from the last batch have already been removed.", success=True)
            try:
                os.remove(undo_path)
            except OSError:
                pass
            return

        reply = QMessageBox.StandardButton.Yes if self._show_custom_question("↩️ Undo Last Batch", f"This will DELETE {len(existing)} note file(s) written by the last batch:\n\n"
            + "\n".join(f"• {os.path.basename(f)}" for f in existing[:10])
            + ("\n..." if len(existing) > 10 else "")
            + "\n\nProceed?") else QMessageBox.StandardButton.No
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        errors = 0
        try:
            cache = CacheDB()
        except Exception:
            cache = None
        for fpath in existing:
            try:
                os.remove(fpath)
                deleted += 1
                # Also remove from the SQLite cache by note_path match.
                if cache is not None:
                    try:
                        cache.cursor.execute(
                            "DELETE FROM processed_repos WHERE note_path = ?", (fpath,)
                        )
                        cache.conn.commit()
                    except Exception:
                        pass
            except Exception:
                errors += 1
        if cache is not None:
            try:
                cache.close()
            except Exception:
                pass

        self.log_message(f"↩️ Undo: deleted {deleted} file(s), {errors} error(s).", "success" if errors == 0 else "warning")
        # Remove the undo file so the same batch can't be undone twice.
        try:
            os.remove(undo_path)
        except OSError:
            pass
        # Refresh the dashboard to reflect the deletion.
        try:
            self.update_dashboard()
        except Exception:
            pass
