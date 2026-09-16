'use client';

import { useQuery } from '@tanstack/react-query';
import { getDecommissioned } from '@/lib/api';
import { Trash2, Cpu, Bot, Loader2 } from 'lucide-react';
import { timeAgo } from '@/lib/utils';
import { useState } from 'react';

export default function DecommissionedPage() {
  const [sourceFilter, setSourceFilter] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['decommissioned', sourceFilter],
    queryFn: () => getDecommissioned(),
    refetchInterval: 60_000,
  });

  const events = data?.events || [];
  const filtered = sourceFilter ? events.filter((e) => e.source === sourceFilter) : events;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
            Decommissioned Repos
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
            {events.length} repo{events.length !== 1 ? 's' : ''} marked as dead
          </p>
        </div>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          className="input"
          style={{ width: '160px' }}
        >
          <option value="">All sources</option>
          <option value="desktop">Desktop</option>
          <option value="bot">Bot</option>
        </select>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <Trash2 className="empty-state-icon" />
            <p className="text-sm">No decommissioned repos</p>
          </div>
        </div>
      ) : (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Reason</th>
                  <th>Source</th>
                  <th>Date</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((event) => {
                  const match = event.url_normalized.match(/github\.com\/([^/]+)\/([^/]+)/);
                  const repoName = match ? `${match[1]}/${match[2]}` : event.url_normalized;
                  return (
                    <tr key={event.id}>
                      <td>
                        <span className="text-sm font-medium mono">{repoName}</span>
                      </td>
                      <td>
                        <span className="badge badge-danger">{event.reason}</span>
                      </td>
                      <td>
                        <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--color-muted)' }}>
                          {event.source === 'bot' ? <Bot className="w-3 h-3" /> : <Cpu className="w-3 h-3" />}
                          {event.source}
                        </span>
                      </td>
                      <td>
                        <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                          {timeAgo(event.decommissioned_at)}
                        </span>
                      </td>
                      <td>
                        <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                          {event.details || '—'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
