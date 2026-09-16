// ========================================
// kv.js — KV cache helpers (dedup hot cache)
// ========================================

const DEDUP_PREFIX = 'dedup:';
const KV_TTL = 3600; // 1 hour

/**
 * Get cached dedup status for a URL.
 * Returns { status, vault_path, updated_at } or null.
 */
export async function cacheGetDedup(kv, urlNormalized) {
  const value = await kv.get(`${DEDUP_PREFIX}${urlNormalized}`, 'json');
  return value;
}

/**
 * Set cached dedup status for a URL.
 */
export async function cacheSetDedup(kv, urlNormalized, data) {
  await kv.put(`${DEDUP_PREFIX}${urlNormalized}`, JSON.stringify({
    status: data.status,
    vault_path: data.vault_path || null,
    category: data.category || null,
    updated_at: new Date().toISOString()
  }), { expirationTtl: KV_TTL });
}

/**
 * Delete cached dedup entry (force refresh from D1)
 */
export async function cacheDeleteDedup(kv, urlNormalized) {
  await kv.delete(`${DEDUP_PREFIX}${urlNormalized}`);
}

/**
 * Get desktop install pairing code from KV (10-min TTL)
 */
export async function cacheGetPairingCode(kv, code) {
  return kv.get(`pair:${code}`, 'json');
}

/**
 * Set desktop install pairing code in KV
 */
export async function cacheSetPairingCode(kv, code, data) {
  await kv.put(`pair:${code}`, JSON.stringify(data), { expirationTtl: 600 }); // 10 min
}

/**
 * Delete pairing code after use
 */
export async function cacheDeletePairingCode(kv, code) {
  await kv.delete(`pair:${code}`);
}
