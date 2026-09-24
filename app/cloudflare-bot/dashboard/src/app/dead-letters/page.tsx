'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getDeadLetters, forgetUrl } from '@/lib/api';
import { Skull, Trash2, Loader2 } from 'lucide-react';
import { timeAgo } from '@/lib/utils';

export default function DeadLettersPage() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['dead-letters'],
    queryFn: getDeadLetters,
    refetchInterval: 60_000,
  });

  const forgetMutation = useMutation({
    mutationFn: forgetUrl,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dead-letters'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  const letters = data?.letters || [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
          Dead Letters
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
          {letters.length} link{letters.length !== 1 ? 's' : ''} that could never be processed
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
        </div>
      ) : letters.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <Skull className="empty-state-icon" />
            <p className="text-sm">No dead letters</p>
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
                  <th>First Attempt</th>
                  <th>Attempts</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {letters.map((letter) => (
                  <tr key={letter.url_normalized}>
                    <td>
                      <span className="mono text-xs" style={{ color: 'var(--color-foreground)' }}>
                        {letter.url_original.length > 60
                          ? letter.url_original.substring(0, 60) + '...'
                          : letter.url_original}
                      </span>
                    </td>
                    <td>
                      <span className="badge badge-warning">{letter.reason}</span>
                    </td>
                    <td>
                      <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                        {timeAgo(letter.first_attempted_at)}
                      </span>
                    </td>
                    <td>
                      <span className="text-sm" style={{ color: 'var(--color-muted)' }}>
                        {letter.attempt_count}
                      </span>
                    </td>
                    <td>
                      <button
                        onClick={() => {
                          if (confirm(`Forget ${letter.url_original}?`)) {
                            forgetMutation.mutate(letter.url_normalized);
                          }
                        }}
                        disabled={forgetMutation.isPending}
                        className="btn btn-ghost btn-icon"
                        style={{ color: 'var(--color-danger)' }}
                        title="Forget"
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
