'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getErrors, clearErrors } from '@/lib/api';
import { AlertCircle, CheckCircle, Loader2 } from 'lucide-react';
import { timeAgo, truncate } from '@/lib/utils';
import { useState } from 'react';

const severityConfig = {
  CRITICAL: { color: '#DC2626', bg: 'rgba(239, 68, 68, 0.08)', badge: 'badge-danger' },
  WARNING: { color: '#D97706', bg: 'rgba(245, 158, 11, 0.08)', badge: 'badge-warning' },
  INFO: { color: '#2563EB', bg: 'rgba(59, 130, 246, 0.08)', badge: 'badge-info' },
  DEBUG: { color: '#71717A', bg: 'rgba(113, 113, 122, 0.08)', badge: 'badge-neutral' },
};

export default function ErrorsPage() {
  const [severityFilter, setSeverityFilter] = useState('');
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['errors', severityFilter],
    queryFn: () => getErrors(severityFilter || undefined),
    refetchInterval: 15_000,
  });

  const clearMutation = useMutation({
    mutationFn: clearErrors,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['errors'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  const errors = data?.errors || [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
            Desktop Errors
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
            {errors.length} recent error{errors.length !== 1 ? 's' : ''} from the desktop app
          </p>
        </div>
        <div className="flex gap-2">
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="input"
            style={{ width: '160px' }}
          >
            <option value="">All severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
            <option value="DEBUG">Debug</option>
          </select>
          <button
            onClick={() => clearMutation.mutate()}
            disabled={clearMutation.isPending || errors.length === 0}
            className="btn btn-outline"
          >
            <CheckCircle className="w-4 h-4" />
            Clear All
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
        </div>
      ) : errors.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <CheckCircle className="empty-state-icon" style={{ color: 'var(--color-success)' }} />
            <p className="text-sm">No errors</p>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {errors.map((error) => {
            const config = severityConfig[error.severity] || severityConfig.DEBUG;
            return (
              <div key={error.id} className="card" style={{ backgroundColor: config.bg, padding: '16px 20px' }}>
                <div className="flex items-start gap-3">
                  <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: config.color }} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`badge ${config.badge}`}>{error.severity}</span>
                      <span className="text-sm font-semibold mono" style={{ color: 'var(--color-foreground)' }}>
                        {error.error_code}
                      </span>
                      <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                        {timeAgo(error.occurred_at)}
                      </span>
                    </div>
                    <p className="text-sm" style={{ color: 'var(--color-foreground)' }}>
                      {truncate(error.message, 200)}
                    </p>
                    {error.details && (
                      <details className="mt-2">
                        <summary className="text-xs cursor-pointer" style={{ color: 'var(--color-muted)' }}>
                          Show details
                        </summary>
                        <pre
                          className="mt-2 p-3 rounded-lg text-xs mono overflow-x-auto"
                          style={{ backgroundColor: 'var(--color-surface-2)', color: 'var(--color-muted)' }}
                        >
                          {error.details}
                        </pre>
                      </details>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
