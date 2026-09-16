'use client';

import { useQuery } from '@tanstack/react-query';
import { getSettings } from '@/lib/api';
import { Cpu, HardDrive, Bell, LogOut, Loader2 } from 'lucide-react';
import { logout, logoutAll } from '@/lib/api';
import { useRouter } from 'next/navigation';

export default function SettingsPage() {
  const router = useRouter();
  const { data, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  const handleLogoutAll = async () => {
    if (confirm('Log out of ALL devices?')) {
      await logoutAll();
      router.push('/login');
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
      </div>
    );
  }
  if (!data) return null;

  const settings = data.settings as Record<string, string>;

  return (
    <div className="space-y-5 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
          Settings
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
          System configuration and account
        </p>
      </div>

      {/* System Status */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title flex items-center gap-2">
            <Cpu className="w-4 h-4" style={{ color: 'var(--color-primary)' }} />
            System Status
          </h2>
        </div>
        <div className="space-y-3">
          <div className="flex justify-between">
            <span className="text-sm" style={{ color: 'var(--color-muted)' }}>Cutover Status</span>
            <span className="text-sm font-medium">
              {settings.cutover_complete === '1' ? '✅ Complete' : '⏳ Pending'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-sm" style={{ color: 'var(--color-muted)' }}>Desktop Status</span>
            <span className="text-sm font-medium">
              {settings.desktop_last_poll
                ? (Date.now() - new Date(settings.desktop_last_poll).getTime() < 10 * 60 * 1000
                    ? '● Online'
                    : '○ Offline')
                : 'Unknown'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-sm" style={{ color: 'var(--color-muted)' }}>Vault Index</span>
            <span className="text-sm font-medium">
              {settings.vault_index_entry_count || '0'} entries
            </span>
          </div>
        </div>
      </div>

      {/* Backup */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title flex items-center gap-2">
            <HardDrive className="w-4 h-4" style={{ color: 'var(--color-success)' }} />
            Backup
          </h2>
        </div>
        <div className="space-y-3">
          <div className="flex justify-between">
            <span className="text-sm" style={{ color: 'var(--color-muted)' }}>Status</span>
            <span className="text-sm font-medium">
              {settings.gdrive_auth_status === 'ok' ? '● Connected' : '○ Disconnected'}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-sm" style={{ color: 'var(--color-muted)' }}>Last Backup</span>
            <span className="text-sm font-medium">
              {settings.gdrive_last_backup || 'Never'}
            </span>
          </div>
        </div>
      </div>

      {/* Notifications */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title flex items-center gap-2">
            <Bell className="w-4 h-4" style={{ color: '#F59E0B' }} />
            Notifications
          </h2>
        </div>
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-sm">Critical DMs</span>
            <span className="badge badge-success">Enabled</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-sm">Warning DMs</span>
            <span className="badge badge-success">Enabled</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-sm">Info DMs</span>
            <span className="badge badge-info">Daily summary</span>
          </div>
        </div>
      </div>

      {/* Session */}
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">Session</h2>
        </div>
        <div className="flex gap-3">
          <button onClick={handleLogout} className="btn btn-outline">
            <LogOut className="w-4 h-4" />
            Log Out
          </button>
          <button onClick={handleLogoutAll} className="btn btn-danger">
            Log Out All Devices
          </button>
        </div>
      </div>
    </div>
  );
}
