'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getBackups, triggerBackup } from '@/lib/api';
import { HardDrive, Upload, Loader2, RefreshCw } from 'lucide-react';
import { formatBytes, timeAgo } from '@/lib/utils';

export default function BackupsPage() {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['backups'],
    queryFn: getBackups,
    refetchInterval: 60_000,
  });

  const backupMutation = useMutation({
    mutationFn: triggerBackup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backups'] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
    },
  });

  const backups = data?.backups || [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
            Backups
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
            {backups.length} backup{backups.length !== 1 ? 's' : ''} available
          </p>
        </div>
        <button
          onClick={() => backupMutation.mutate()}
          disabled={backupMutation.isPending}
          className="btn btn-accent"
        >
          {backupMutation.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Upload className="w-4 h-4" />
          )}
          Backup Now
        </button>
      </div>

      {backupMutation.isSuccess && (
        <div
          className="p-3 rounded-lg text-sm"
          style={{
            backgroundColor: 'rgba(16, 185, 129, 0.08)',
            color: '#059669',
            border: '1px solid rgba(16, 185, 129, 0.2)',
          }}
        >
          Backup triggered — desktop will upload on next sync cycle.
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
        </div>
      ) : backups.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <HardDrive className="empty-state-icon" />
            <p className="text-sm">No backups yet</p>
          </div>
        </div>
      ) : (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>File Name</th>
                  <th>Size</th>
                  <th>Repos</th>
                  <th>Created</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.backup_id}>
                    <td>
                      <span className="mono text-xs">{backup.file_name}</span>
                    </td>
                    <td>
                      <span className="text-sm" style={{ color: 'var(--color-foreground)' }}>
                        {formatBytes(backup.file_size_bytes)}
                      </span>
                    </td>
                    <td>
                      <span className="text-sm" style={{ color: 'var(--color-muted)' }}>
                        {backup.repo_count}
                      </span>
                    </td>
                    <td>
                      <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                        {timeAgo(backup.created_at)}
                      </span>
                    </td>
                    <td>
                      {backup.status === 'uploaded' ? (
                        <span className="badge badge-success">Uploaded</span>
                      ) : backup.status === 'uploading' ? (
                        <span className="badge badge-info">
                          <RefreshCw className="w-3 h-3 animate-spin" />
                          Uploading
                        </span>
                      ) : (
                        <span className="badge badge-danger">Failed</span>
                      )}
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
