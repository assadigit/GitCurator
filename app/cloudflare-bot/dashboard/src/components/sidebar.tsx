'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Clock,
  FolderOpen,
  Trash2,
  Skull,
  AlertCircle,
  HardDrive,
  Settings,
  Github,
  Moon,
  Sun,
} from 'lucide-react';
import { useTheme } from 'next-themes';
import { useState } from 'react';

const navItems = [
  { href: '/overview', label: 'Overview', icon: LayoutDashboard },
  { href: '/pending', label: 'Pending', icon: Clock },
  { href: '/vault', label: 'Vault', icon: FolderOpen },
  { href: '/decommissioned', label: 'Decommissioned', icon: Trash2 },
  { href: '/dead-letters', label: 'Dead Letters', icon: Skull },
  { href: '/errors', label: 'Errors', icon: AlertCircle },
  { href: '/backups', label: 'Backups', icon: HardDrive },
  { href: '/settings', label: 'Settings', icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);

  // Don't show sidebar on login page
  if (pathname === '/login') {
    return null;
  }

  return (
    <aside
      className="flex flex-col h-screen border-r"
      style={{
        width: collapsed ? '64px' : '240px',
        backgroundColor: 'var(--color-sidebar)',
        borderColor: 'var(--color-sidebar-border)',
        transition: 'width 0.2s ease',
      }}
    >
      {/* Logo header */}
      <div
        className="flex items-center gap-3 px-4 py-5 border-b"
        style={{ borderColor: 'var(--color-sidebar-border)' }}
      >
        <div
          className="flex items-center justify-center rounded-lg flex-shrink-0"
          style={{
            width: '36px',
            height: '36px',
            backgroundColor: 'var(--color-primary)',
          }}
        >
          <Github className="w-5 h-5 text-white" />
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <h1 className="text-sm font-semibold truncate" style={{ color: 'var(--color-sidebar-foreground)' }}>
              Curator
            </h1>
            <p className="text-xs truncate" style={{ color: 'var(--color-sidebar-muted)' }}>
              Dashboard
            </p>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname?.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              style={{
                backgroundColor: isActive ? 'var(--color-sidebar-active)' : 'transparent',
                color: isActive ? 'var(--color-sidebar-active-fg)' : 'var(--color-sidebar-muted)',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.backgroundColor = 'var(--color-surface-2)';
                  e.currentTarget.style.color = 'var(--color-sidebar-foreground)';
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.backgroundColor = 'transparent';
                  e.currentTarget.style.color = 'var(--color-sidebar-muted)';
                }
              }}
              title={collapsed ? item.label : undefined}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div
        className="px-2 py-3 border-t space-y-0.5"
        style={{ borderColor: 'var(--color-sidebar-border)' }}
      >
        {/* Theme toggle */}
        <button
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium w-full transition-colors"
          style={{ color: 'var(--color-sidebar-muted)' }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--color-surface-2)';
            e.currentTarget.style.color = 'var(--color-sidebar-foreground)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent';
            e.currentTarget.style.color = 'var(--color-sidebar-muted)';
          }}
          title={collapsed ? 'Toggle theme' : undefined}
        >
          {theme === 'dark' ? <Sun className="w-4 h-4 flex-shrink-0" /> : <Moon className="w-4 h-4 flex-shrink-0" />}
          {!collapsed && <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>}
        </button>

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium w-full transition-colors"
          style={{ color: 'var(--color-sidebar-muted)' }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--color-surface-2)';
            e.currentTarget.style.color = 'var(--color-sidebar-foreground)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent';
            e.currentTarget.style.color = 'var(--color-sidebar-muted)';
          }}
          title="Toggle sidebar"
        >
          {collapsed ? <span className="text-xs">›</span> : <span className="text-xs">‹ Collapse</span>}
        </button>

        {!collapsed && (
          <div className="px-3 pt-2">
            <p className="text-[10px]" style={{ color: 'var(--color-sidebar-muted)' }}>
              v1.0.0 · Cloudflare Edge
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
