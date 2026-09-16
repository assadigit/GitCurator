'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getPending, markDecommissioned } from '@/lib/api';
import { formatStars, truncate, timeAgo } from '@/lib/utils';
import { Star, Trash2, Search, Loader2, ExternalLink } from 'lucide-react';
import { useState } from 'react';

export default function PendingPage() {
  const [search, setSearch] = useState('');
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['pending'],
    queryFn: getPending,
    refetchInterval: 30_000,
  });

  const decommissionMutation = useMutation({
    mutationFn: markDecommissioned,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
      </div>
    );
  }

  const pending = data?.pending || [];
  const filtered = search
    ? pending.filter((p) =>
        `${p.github_owner}/${p.github_repo}`.toLowerCase().includes(search.toLowerCase())
      )
    : pending;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
            Pending Links
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
            {pending.length} link{pending.length !== 1 ? 's' : ''} waiting for desktop processing
          </p>
        </div>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--color-muted)' }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search..."
            className="input"
            style={{ width: '240px', paddingLeft: '36px' }}
          />
        </div>
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <Clock className="empty-state-icon" />
            <p className="text-sm">No pending links</p>
          </div>
        </div>
      ) : (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Stars</th>
                  <th>Description</th>
                  <th>Forwarded</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((link) => (
                  <tr key={link.url_normalized}>
                    <td>
                      <a
                        href={`https://github.com/${link.github_owner}/${link.github_repo}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-sm font-medium link"
                      >
                        {link.github_owner}/{link.github_repo}
                        <ExternalLink className="w-3 h-3 opacity-50" />
                      </a>
                    </td>
                    <td>
                      {link.github_metadata?.stars ? (
                        <span className="flex items-center gap-1 text-sm" style={{ color: 'var(--color-foreground)' }}>
                          <Star className="w-3 h-3" style={{ color: '#F59E0B' }} />
                          {formatStars(link.github_metadata.stars)}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--color-muted-2)' }}>—</span>
                      )}
                    </td>
                    <td className="max-w-xs">
                      <span className="text-sm" style={{ color: 'var(--color-muted)' }}>
                        {link.github_metadata?.description
                          ? truncate(link.github_metadata.description, 70)
                          : '—'}
                      </span>
                    </td>
                    <td>
                      <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                        {timeAgo(link.first_seen_at)}
                      </span>
                    </td>
                    <td>
                      <button
                        onClick={() => decommissionMutation.mutate(link.url_normalized)}
                        disabled={decommissionMutation.isPending}
                        className="btn btn-ghost btn-icon"
                        style={{ color: 'var(--color-danger)' }}
                        title="Mark decommissioned"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Clock({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}
