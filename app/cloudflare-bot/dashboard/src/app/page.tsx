'use client';

import { useState } from 'react';
import { requestMagicLink } from '@/lib/api';
import { Github, ArrowRight, Loader2, MessageCircle } from 'lucide-react';

export default function LoginPage() {
  const [userId, setUserId] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userId.trim()) {
      setError('Please enter your Telegram user ID');
      return;
    }

    setLoading(true);
    setError('');
    setMessage('');

    try {
      const result = await requestMagicLink(userId.trim());
      if (result.success) {
        setMessage('Magic link sent! Check your Telegram DMs from the bot.');
      } else {
        setError(result.message || 'Failed to send magic link');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ backgroundColor: 'var(--color-background)' }}>
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div
            className="flex items-center justify-center rounded-2xl mb-4 shadow-lg"
            style={{
              width: '56px',
              height: '56px',
              backgroundColor: 'var(--color-primary)',
              boxShadow: '0 8px 24px -4px rgba(99, 102, 241, 0.4)',
            }}
          >
            <Github className="w-7 h-7 text-white" />
          </div>
          <h1 className="text-xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
            Curator Dashboard
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
            Sign in with your Telegram account
          </p>
        </div>

        {/* Card */}
        <div className="card">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                className="block text-xs font-medium mb-2"
                style={{ color: 'var(--color-foreground)' }}
              >
                Telegram User ID
              </label>
              <input
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="e.g., 123456789"
                className="input"
                disabled={loading}
                autoFocus
              />
              <a
                href="https://t.me/userinfobot"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs mt-2 link"
              >
                <MessageCircle className="w-3 h-3" />
                Get your ID from @userinfobot
              </a>
            </div>

            {error && (
              <div
                className="p-3 rounded-lg text-sm"
                style={{
                  backgroundColor: 'rgba(239, 68, 68, 0.08)',
                  color: '#DC2626',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                }}
              >
                {error}
              </div>
            )}

            {message && (
              <div
                className="p-3 rounded-lg text-sm"
                style={{
                  backgroundColor: 'rgba(16, 185, 129, 0.08)',
                  color: '#059669',
                  border: '1px solid rgba(16, 185, 129, 0.2)',
                }}
              >
                {message}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="btn btn-primary w-full"
              style={{ padding: '10px 16px', fontSize: '14px' }}
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Sending...
                </>
              ) : (
                <>
                  Send Magic Link
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>
        </div>

        {/* Help text */}
        <p className="text-xs text-center mt-6 leading-relaxed" style={{ color: 'var(--color-muted)' }}>
          The bot will DM you a one-time login link.<br />
          Click it to log in — no password needed.
        </p>
      </div>
    </div>
  );
}
