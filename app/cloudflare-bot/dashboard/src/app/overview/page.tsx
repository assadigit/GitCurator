'use client';

import { useQuery } from '@tanstack/react-query';
import { getOverview } from '@/lib/api';
import { formatStars, timeAgo, truncate } from '@/lib/utils';
import {
  Clock,
  CheckCircle,
  Trash2,
  Skull,
  Activity,
  Cpu,
  HardDrive,
  RefreshCw,
  Loader2,
} from 'lucide-react';

export default function OverviewPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['overview'],
    queryFn: getOverview,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
      </div>
    );
  }

  if (!data) return null;

  const { stats, recent_activity } = data;

  const cards = [
    {
      label: 'Pending',
      value: stats.pending,
      icon: Clock,
      iconBg: 'rgba(245, 158, 11, 0.12)',
      iconColor: '#D97706',
    },
    {
      label: 'In Vault',
      value: stats.inVault,
      icon: CheckCircle,
      iconBg: 'rgba(16, 185, 129, 0.12)',
      iconColor: '#059669',
    },
    {
      label: 'Decommissioned',
      value: stats.decommissioned,
      icon: Trash2,
      iconBg: 'rgba(239, 68, 68, 0.12)',
      iconColor: '#DC2626',
    },
    {
      label: 'Dead Letters',
      value: stats.deadLetters,
      icon: Skull,
      iconBg: 'rgba(113, 113, 122, 0.12)',
      iconColor: '#71717A',
    },
  ];

  const desktopOnline = stats.lastPoll
    ? Date.now() - new Date(stats.lastPoll).getTime() < 10 * 60 * 1000
    : false;

  const gdriveOk = stats.lastBackup != null;

  const systemStatus = [
    {
      icon: Cpu,
      label: 'Desktop',
      online: desktopOnline,
      detail: stats.lastPoll ? timeAgo(stats.lastPoll) : 'Never',
    },
    {
      icon: HardDrive,
      label: 'Backup',
      online: gdriveOk,
      detail: stats.lastBackup ? timeAgo(stats.lastBackup) : 'Never',
    },
    {
      icon: RefreshCw,
      label: 'Last Sync',
      online: stats.lastSync != null,
      detail: stats.lastSync ? timeAgo(stats.lastSync) : 'Never',
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
          Overview
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
          System status and recent activity
        </p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="stat-card">
              <div className="stat-label">{card.label}</div>
              <div className="stat-value">{card.value}</div>
              <div className="stat-icon" style={{ backgroundColor: card.iconBg }}>
                <Icon className="w-4 h-4" style={{ color: card.iconColor }} />
              </div>
            </div>
          );
        })}
      </div>

      {/* Two columns: System Status + Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* System Status */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">System Status</h2>
            <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
              Total: {stats.total} links
            </span>
          </div>
          <div className="space-y-3">
            {systemStatus.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.label} className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Icon
                      className="w-4 h-4"
                      style={{ color: item.online ? 'var(--color-success)' : 'var(--color-muted-2)' }}
                    />
                    <span className="text-sm font-medium" style={{ color: 'var(--color-foreground)' }}>
                      {item.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`dot ${item.online ? 'dot-success' : 'dot-muted'}`}
                    />
                    <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                      {item.detail}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Recent Activity */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title flex items-center gap-2">
              <Activity className="w-4 h-4" style={{ color: 'var(--color-primary)' }} />
              Recent Activity
            </h2>
            <span className="text-xs" style={{ color: 'var(--color-muted)' }}>Last 24h</span>
          </div>
          {recent_activity.length === 0 ? (
            <div className="empty-state py-8">
              <p className="text-sm">No recent activity</p>
            </div>
          ) : (
            <div className="max-h-72 overflow-y-auto">
              {recent_activity.slice(0, 15).map((activity) => (
                <div key={activity.id} className="activity-item">
                  <span className="activity-time">
                    {new Date(activity.occurred_at).toLocaleTimeString('en-US', {
                      hour: '2-digit',
                      minute: '2-digit',
                      hour12: false,
                    })}
                  </span>
                  <span className="activity-text">{truncate(activity.message, 100)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
