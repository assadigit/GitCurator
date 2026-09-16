'use client';

import { useQuery } from '@tanstack/react-query';
import { getVault } from '@/lib/api';
import { Search, FolderOpen, Tag, Loader2, ExternalLink } from 'lucide-react';
import { useState } from 'react';

export default function VaultPage() {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['vault', search, category],
    queryFn: () => getVault(search || undefined, category || undefined),
    refetchInterval: 60_000,
  });

  const entries = data?.entries || [];
  const categories = [...new Set(entries.map((e) => e.category).filter(Boolean))];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--color-foreground)' }}>
          Vault
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--color-muted)' }}>
          {data?.count || 0} repos in your Obsidian vault
        </p>
      </div>

      <div className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--color-muted)' }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by URL, title, path, or category..."
            className="input"
            style={{ paddingLeft: '36px' }}
          />
        </div>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="input"
          style={{ width: '180px' }}
        >
          <option value="">All categories</option>
          {categories.map((cat) => (
            <option key={cat} value={cat}>{cat}</option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-24">
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--color-muted)' }} />
        </div>
      ) : entries.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <FolderOpen className="empty-state-icon" />
            <p className="text-sm">No results found</p>
          </div>
        </div>
      ) : (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Title</th>
                  <th>Category</th>
                  <th>Vault Path</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => {
                  const match = entry.url_normalized.match(/github\.com\/([^/]+)\/([^/]+)/);
                  const repoName = match ? `${match[1]}/${match[2]}` : entry.url_normalized;
                  return (
                    <tr key={entry.url_normalized}>
                      <td>
                        <a
                          href={`https://github.com/${match?.[1]}/${match?.[2]}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-sm font-medium link"
                        >
                          {repoName}
                          <ExternalLink className="w-3 h-3 opacity-50" />
                        </a>
                      </td>
                      <td>
                        <span className="text-sm" style={{ color: 'var(--color-muted)' }}>
                          {entry.title || '—'}
                        </span>
                      </td>
                      <td>
                        {entry.category && (
                          <span className="badge badge-info">
                            <Tag className="w-3 h-3" />
                            {entry.category}
                          </span>
                        )}
                      </td>
                      <td>
                        <span className="mono text-xs" style={{ color: 'var(--color-muted)' }}>
                          {entry.vault_path}
                        </span>
                      </td>
                      <td>
                        <span className="text-xs" style={{ color: 'var(--color-muted)' }}>
                          {new Date(entry.last_updated_at).toLocaleDateString()}
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
