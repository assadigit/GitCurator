-- ========================================
-- v0.22.0 migration — the not_github dead-letter tail → ledger
-- ========================================
-- The v0.01 worker dead-lettered every non-GitHub URL it ever received
-- (reason='not_github') while replying "Only GitHub links are tracked".
-- The bot now accepts website links (url_type='non_github', processed
-- into the Websites vault by the desktop). This one-time migration moves
-- those wrongly-rejected links into ever_seen_ledger so re-sending them
-- reports "Already pending" instead of "dead letter", and /pending,
-- /api/pending and the dashboard see them.
--
-- Idempotent: INSERT OR IGNORE (the /api/backfill path may have created
-- some non_github ledger rows already) + the DELETE only touches
-- reason='not_github' rows. Run ONCE, then verify with:
--   SELECT url_type, COUNT(*) FROM ever_seen_ledger GROUP BY url_type;
--   SELECT reason, COUNT(*) FROM dead_letters WHERE resolved=0 GROUP BY reason;
-- ========================================

INSERT OR IGNORE INTO ever_seen_ledger
  (url_normalized, url_original, url_type, github_owner, github_repo,
   first_seen_at, last_seen_at, forward_count,
   telegram_message_id, telegram_chat_id, bot_reply_message_id)
SELECT
  d.url_normalized, d.url_original, 'non_github', NULL, NULL,
  d.first_attempted_at, d.last_attempted_at, 1,
  d.telegram_message_id, NULL, NULL
FROM dead_letters d
WHERE d.reason = 'not_github';

DELETE FROM dead_letters WHERE reason = 'not_github';
