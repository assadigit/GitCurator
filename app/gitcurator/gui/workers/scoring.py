#!/usr/bin/env python3
"""gitcurator.gui.workers.scoring — the scoring mixin.

Credibility calculation, note building and the rating helpers (verbatim
methods of the original ProcessingWorker).

"""

from gitcurator.gui._qt import *  # noqa: F401,F403
from gitcurator.gui.workers._deps import *  # noqa: F401,F403
__all__ = ["ScoringMixin"]


class ScoringMixin:
    """ScoringMixin — see module docstring (methods are verbatim moves)."""


    def _calculate_credibility(self, org_rep, stars, commits, repo):
        if stars > 10000:
            stars_score = 10
        elif stars > 5000:
            stars_score = 8
        elif stars > 1000:
            stars_score = 6
        elif stars > 100:
            stars_score = 4
        elif stars > 10:
            stars_score = 3
        else:
            stars_score = 1

        if commits > 50:
            activity_score = 10
        elif commits > 20:
            activity_score = 7
        elif commits > 5:
            activity_score = 4
        else:
            activity_score = 2

        doc_score = 5
        try:
            if repo.has_issues:
                doc_score += 1
            if repo.license:
                doc_score += 1
            if repo.has_wiki:
                doc_score += 1
            if repo.description and len(repo.description) > 50:
                doc_score += 1
            if repo.get_readme():
                doc_score += 1
        except:
            pass
        doc_score = min(doc_score, 10)

        # Weighted sum on 0-10 scale, then *10 to get 0-100
        raw = (org_rep * 0.4) + (stars_score * 0.25) + (activity_score * 0.2) + (doc_score * 0.15)
        score = round(raw * 10, 1)
        # Famous company special case: minimum 90/100 credibility
        if org_rep >= 10:
            score = max(score, 90.0)
        return score

    def _build_note(self, url, repo_name, owner, org_name, stars, forks, commit_count,
                    cred_score, org_rep, summary, tags, category_key, confidence,
                    how_it_works, core_value, features, difference, banner_path=None,
                    primary_language="", languages=None, short_summary="",
                    latest_release_date="",
                    quality_issues=None, is_low_quality=False):
        """v30 — Fix (sanitize LLM output into frontmatter, W12/T4): delegated
        to note_builder.build_note. Every value that lands in the YAML
        frontmatter (tags, aliases, org, url, category, languages) is now
        sanitized against YAML injection — the old template interpolated
        raw LLM strings straight into ``tags: [{', '.join(tags)}]``."""
        return _note_builder.build_note(
            url=url, repo_name=repo_name, owner=owner, org_name=org_name,
            stars=stars, forks=forks, commit_count=commit_count,
            cred_score=cred_score, org_rep=org_rep, summary=summary,
            tags=tags, category_key=category_key, confidence=confidence,
            how_it_works=how_it_works, core_value=core_value,
            features=features, difference=difference, banner_path=banner_path,
            primary_language=primary_language, languages=languages,
            short_summary=short_summary,
            latest_release_date=latest_release_date,
            quality_issues=quality_issues,
            is_low_quality=is_low_quality,
        )

    def _rep_to_str(self, rep):
        return _note_builder.rep_to_str(rep)

    def _score_to_rating(self, score):
        return _note_builder.score_to_rating(score)
