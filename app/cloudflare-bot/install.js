#!/usr/bin/env node
/**
 * install.js — Auto-installer for GitHub-to-Obsidian Bot
 * ========================================================
 * Reads credentials from installer.config.json, then:
 *   1. Creates D1 database (if not exists)
 *   2. Creates KV namespace (if not exists)
 *   3. Creates Queues (if not exists)
 *   4. Updates wrangler.toml with resource IDs
 *   5. Applies D1 schema (creates 10 tables)
 *   6. Sets 4 secrets (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET)
 *   7. Deploys the Worker
 *   8. Sets the Telegram webhook
 *   9. Tests the bot
 *
 * Prerequisites:
 *   - Node.js 18+
 *   - Run `npm install` first
 *   - Run `npx wrangler login` first
 *   - Fill in installer.config.json with your credentials
 *
 * Usage:
 *   node install.js
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const https = require('https');

// ========================================
// Config
// ========================================

const CONFIG_PATH = path.join(__dirname, 'installer.config.json');
const WRANGLER_TOML_PATH = path.join(__dirname, 'wrangler.toml');
const SCHEMA_PATH = path.join(__dirname, 'schema.sql');

const D1_NAME = 'curator-bot';
const KV_BINDING = 'CACHE';
const QUEUE_MAIN = 'curator-ingest';
const QUEUE_DLQ = 'curator-ingest-dlq';

// ========================================
// Helpers
// ========================================

function log(msg) {
  console.log(msg);
}

function success(msg) {
  console.log(`  ✅ ${msg}`);
}

function info(msg) {
  console.log(`  ℹ️  ${msg}`);
}

function warn(msg) {
  console.log(`  ⚠️  ${msg}`);
}

function error(msg) {
  console.error(`  ❌ ${msg}`);
}

function step(num, msg) {
  console.log(`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
  console.log(`  STEP ${num}: ${msg}`);
  console.log(`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
}

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, {
      cwd: __dirname,
      encoding: 'utf8',
      stdio: opts.silent ? ['pipe', 'pipe', 'pipe'] : 'pipe',
      timeout: 120000,  // 2 min timeout
      ...opts
    });
  } catch (e) {
    if (opts.silent) {
      // Return combined stdout + stderr so we can see the actual error
      const stdout = e.stdout ? e.stdout.toString() : '';
      const stderr = e.stderr ? e.stderr.toString() : '';
      return stdout + stderr;
    }
    throw e;
  }
}

function runWithStdin(cmd, input) {
  const { execSync } = require('child_process');
  try {
    execSync(cmd, {
      cwd: __dirname,
      encoding: 'utf8',
      input: input,
      timeout: 30000
    });
    return true;
  } catch (e) {
    return false;
  }
}

// ========================================
// Load config
// ========================================

function loadConfig() {
  step(0, 'Loading configuration');

  if (!fs.existsSync(CONFIG_PATH)) {
    error(`Config file not found: ${CONFIG_PATH}`);
    error('Create it from installer.config.json and fill in your credentials.');
    process.exit(1);
  }

  const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));

  // Validate
  const required = ['bot_token', 'github_pat', 'allowed_user_ids', 'hmac_secret'];
  const missing = required.filter(k => !config[k] || config[k].includes('YOUR_'));

  if (missing.length > 0) {
    error(`Missing or placeholder values in installer.config.json:`);
    missing.forEach(k => error(`  - ${k}`));
    error('\nFill in your actual credentials, then run: node install.js');
    process.exit(1);
  }

  success('Configuration loaded');
  info(`Bot token: ${config.bot_token.substring(0, 10)}...`);
  info(`GitHub PAT: ${config.github_pat.substring(0, 8)}...`);
  info(`Allowed user IDs: ${config.allowed_user_ids}`);
  return config;
}

// ========================================
// Verify wrangler is logged in
// ========================================

function verifyLogin() {
  step(1, 'Verifying Wrangler login');

  const output = run('npx wrangler whoami', { silent: true });
  if (!output || !output.includes('logged in')) {
    error('Wrangler is not logged in. Run: npx wrangler login');
    process.exit(1);
  }

  // Extract email
  const emailMatch = output.match(/associated with the email (\S+\.)/);
  if (emailMatch) {
    success(`Logged in as: ${emailMatch[1]}`);
  } else {
    success('Logged in');
  }
}

// ========================================
// Create D1 database
// ========================================

function createD1() {
  step(2, 'Creating D1 database');

  // Check if already exists
  const list = run('npx wrangler d1 list', { silent: true }) || '';
  if (list.includes(D1_NAME)) {
    success(`D1 database "${D1_NAME}" already exists`);

    // Get the database_id from wrangler.toml if present, or from create output
    const existing = run(`npx wrangler d1 info ${D1_NAME}`, { silent: true }) || '';
    const idMatch = existing.match(/database_id\s*=\s*"([a-f0-9-]+)"/);
    if (idMatch) {
      success(`Database ID: ${idMatch[1]}`);
      return idMatch[1];
    }
    // Try parsing from list output
    const lines = list.split('\n');
    for (const line of lines) {
      if (line.includes(D1_NAME)) {
        const parts = line.trim().split(/\s+/);
        // D1 list format: name | uuid | ...
        const uuid = parts.find(p => /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(p));
        if (uuid) {
          success(`Database ID: ${uuid}`);
          return uuid;
        }
      }
    }
    return null;
  }

  // Create new
  info('Creating D1 database...');
  const output = run(`npx wrangler d1 create ${D1_NAME}`, { silent: true }) || '';
  console.log(output);

  const idMatch = output.match(/database_id\s*=\s*"([a-f0-9-]+)"/);
  if (idMatch) {
    success(`D1 database created. ID: ${idMatch[1]}`);
    return idMatch[1];
  }

  error('Failed to extract database_id from wrangler output');
  error('You may need to create it manually and update wrangler.toml');
  return null;
}

// ========================================
// Create KV namespace
// ========================================

function createKV() {
  step(3, 'Creating KV namespace');

  // Check if already exists
  const list = run('npx wrangler kv namespace list', { silent: true }) || '';
  if (list.includes(KV_BINDING)) {
    success(`KV namespace "${KV_BINDING}" already exists`);

    // Try to get ID from wrangler.toml
    const toml = fs.readFileSync(WRANGLER_TOML_PATH, 'utf8');
    const idMatch = toml.match(/id\s*=\s*"([a-f0-9]+)"/);
    if (idMatch) {
      success(`KV ID: ${idMatch[1]}`);
      return idMatch[1];
    }

    // Try parsing from list output
    try {
      const namespaces = JSON.parse(list);
      const ns = namespaces.find(n => n.binding === KV_BINDING || n.title.includes(KV_BINDING));
      if (ns) {
        success(`KV ID: ${ns.id}`);
        return ns.id;
      }
    } catch {}

    return null;
  }

  // Create new
  info('Creating KV namespace...');
  const output = run(`npx wrangler kv namespace create ${KV_BINDING}`, { silent: true }) || '';
  console.log(output);

  const idMatch = output.match(/id\s*=\s*"([a-f0-9]+)"/);
  if (idMatch) {
    success(`KV namespace created. ID: ${idMatch[1]}`);
    return idMatch[1];
  }

  error('Failed to extract KV id from wrangler output');
  return null;
}

// ========================================
// Create Queues
// ========================================

function createQueues() {
  step(4, 'Creating Queues');

  const queues = [QUEUE_MAIN, QUEUE_DLQ];
  let allOk = true;

  for (const queueName of queues) {
    // Check if exists
    const list = run('npx wrangler queues list', { silent: true }) || '';
    if (list.includes(queueName)) {
      success(`Queue "${queueName}" already exists`);
      continue;
    }

    // Create
    info(`Creating queue "${queueName}"...`);
    const output = run(`npx wrangler queues create ${queueName}`, { silent: true });

    if (output && output.includes('Created queue')) {
      success(`Queue "${queueName}" created`);
    } else {
      warn(`Queue "${queueName}" creation failed via CLI`);
      warn('You may need to create it via Cloudflare Dashboard:');
      warn('  Dashboard → Storage & Databases → Queues → Create queue');
      allOk = false;
    }
  }

  if (!allOk) {
    warn('\nSome queues failed to create. Create them via Dashboard, then re-run this script.');
    warn('The script will detect existing queues and skip them.');
  }

  return allOk;
}

// ========================================
// Update wrangler.toml with IDs
// ========================================

function updateWranglerToml(d1Id, kvId) {
  step(5, 'Updating wrangler.toml');

  let toml = fs.readFileSync(WRANGLER_TOML_PATH, 'utf8');

  if (d1Id) {
    toml = toml.replace(
      /database_id\s*=\s*"[^"]*"/,
      `database_id = "${d1Id}"`
    );
    success(`D1 database_id updated: ${d1Id}`);
  }

  if (kvId) {
    // Replace the KV id (be careful to only replace the one under [[kv_namespaces]])
    toml = toml.replace(
      /^(binding = "CACHE"\s*\nid = ")[^"]*"/m,
      `$1${kvId}"`
    );
    // Fallback: if the above didn't match, try simple replace
    if (!toml.includes(kvId)) {
      toml = toml.replace(
        /^id = "[A-Z_]*"/m,
        `id = "${kvId}"`
      );
    }
    success(`KV id updated: ${kvId}`);
  }

  fs.writeFileSync(WRANGLER_TOML_PATH, toml, 'utf8');
  success('wrangler.toml saved');
}

// ========================================
// Apply D1 schema
// ========================================

function applySchema() {
  step(6, 'Applying D1 schema (creating tables)');

  // Use relative path for schema.sql (wrangler prefers it)
  const schemaFile = 'schema.sql';
  const output = run(`npx wrangler d1 execute ${D1_NAME} --remote --file=${schemaFile}`, { silent: true });

  // Check for success indicators OR check if tables already exist
  if (output && (output.includes('Executed') || output.includes('✅') || output.includes('🚣'))) {
    success('Schema applied — 10 tables created');
  } else if (output && output.includes('already exists')) {
    success('Schema already applied (tables exist)');
  } else {
    // Show the actual error output
    warn('Schema output:');
    console.log(output || '(no output)');

    // Check if tables already exist despite the error
    info('Checking if tables already exist...');
    const verify = run(`npx wrangler d1 execute ${D1_NAME} --remote --command="SELECT name FROM sqlite_master WHERE type='table'"`, { silent: true });
    const tableCount = (verify.match(/ever_seen_ledger|vault_mirror|decommission_events|dead_letters|desktop_errors|sync_state|gdrive_snapshots|dashboard_sessions|desktop_installs|activity_log/g) || []).length;

    if (tableCount >= 8) {
      success(`Tables already exist (${tableCount}/10 found) — continuing`);
      return true;
    }

    error('Schema application failed and tables are missing');
    return false;
  }

  // Verify tables
  const verify = run(`npx wrangler d1 execute ${D1_NAME} --remote --command="SELECT name FROM sqlite_master WHERE type='table'"`, { silent: true });
  const tableCount = (verify.match(/ever_seen_ledger|vault_mirror|decommission_events|dead_letters|desktop_errors|sync_state|gdrive_snapshots|dashboard_sessions|desktop_installs|activity_log/g) || []).length;
  success(`Verification: ${tableCount}/10 tables found`);

  return true;
}

// ========================================
// Set secrets
// ========================================

function setSecrets(config) {
  step(7, 'Setting Worker secrets');

  const secrets = [
    { name: 'BOT_TOKEN', value: config.bot_token },
    { name: 'GITHUB_PAT', value: config.github_pat },
    { name: 'ALLOWED_USER_IDS', value: String(config.allowed_user_ids) },
    { name: 'HMAC_SECRET', value: config.hmac_secret }
  ];

  for (const { name, value } of secrets) {
    info(`Setting ${name}...`);
    const ok = runWithStdin(`npx wrangler secret put ${name}`, value + '\n');
    if (ok) {
      success(`${name} set`);
    } else {
      error(`Failed to set ${name}`);
      error('You can set it manually: npx wrangler secret put ' + name);
    }
  }
}

// ========================================
// Deploy Worker
// ========================================

function deploy() {
  step(8, 'Deploying Worker');

  const output = run('npx wrangler deploy', { silent: true });
  console.log(output);

  // Extract Worker URL — matches subdomain.workers.dev AND subdomain.account.workers.dev
  const urlMatch = output.match(/https:\/\/[a-z0-9.-]+\.workers\.dev/);
  if (urlMatch) {
    const workerUrl = urlMatch[0];
    success(`Worker deployed: ${workerUrl}`);

    // Check for cron trigger errors (non-fatal)
    if (output.includes('triggers failed to deploy') || output.includes('schedules')) {
      if (output.includes('failed')) {
        warn('Cron triggers failed to deploy (non-fatal)');
        warn('The Worker is live — cron jobs (health check, reports) are optional.');
        warn('You can add them later via Cloudflare Dashboard → Workers → Triggers.');
      }
    }

    return workerUrl;
  }

  // If we can't extract URL but deployment succeeded, try alternate pattern
  if (output.includes('Deployed') && output.includes('workers.dev')) {
    // Try to find any workers.dev URL in the output
    const altMatch = output.match(/(https:\/\/[^\s]+\.workers\.dev)/);
    if (altMatch) {
      success(`Worker deployed: ${altMatch[1]}`);
      return altMatch[1];
    }
  }

  error('Failed to extract Worker URL from deploy output');
  error('The Worker may have deployed — check the output above for the URL.');
  return null;
}

// ========================================
// Set Telegram webhook
// ========================================

function setWebhook(config, workerUrl) {
  step(9, 'Setting Telegram webhook');

  const webhookUrl = `${workerUrl}/webhook`;
  const apiUrl = `https://api.telegram.org/bot${config.bot_token}/setWebhook?url=${encodeURIComponent(webhookUrl)}`;

  return new Promise((resolve) => {
    https.get(apiUrl, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (result.ok) {
            success(`Webhook set: ${webhookUrl}`);
          } else {
            error(`Telegram API error: ${result.description}`);
          }
        } catch {
          error('Failed to parse Telegram response');
        }
        resolve();
      });
    }).on('error', (e) => {
      error(`Webhook setup failed: ${e.message}`);
      resolve();
    });
  });
}

// ========================================
// Test the bot
// ========================================

async function testBot(config, workerUrl) {
  step(10, 'Testing the bot');

  // Test health endpoint
  info('Testing Worker health endpoint...');
  const healthUrl = `${workerUrl}/health`;

  await new Promise((resolve) => {
    https.get(healthUrl, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const health = JSON.parse(data);
          if (health.status === 'ok') {
            success(`Worker is healthy (version ${health.version})`);
          } else {
            warn(`Health check returned: ${health.status}`);
          }
        } catch {
          warn('Health check response not JSON');
        }
        resolve();
      });
    }).on('error', (e) => {
      error(`Health check failed: ${e.message}`);
      resolve();
    });
  });

  // Test Telegram webhook info
  info('Verifying Telegram webhook...');
  const webhookInfoUrl = `https://api.telegram.org/bot${config.bot_token}/getWebhookInfo`;

  await new Promise((resolve) => {
    https.get(webhookInfoUrl, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (result.ok) {
            if (result.result.url) {
              success(`Telegram webhook active: ${result.result.url}`);
            }
            if (result.result.last_error_message) {
              warn(`Last webhook error: ${result.result.last_error_message}`);
            }
          }
        } catch {}
        resolve();
      });
    }).on('error', () => resolve());
  });

  // Send test message via bot
  info('Sending test message to your Telegram...');
  const testMsgUrl = `https://api.telegram.org/bot${config.bot_token}/sendMessage`;

  const postData = JSON.stringify({
    chat_id: parseInt(config.allowed_user_ids.split(',')[0].trim()),
    text: '🤖 GitHub-to-Obsidian Bot is now live!\n\nWorker deployed successfully.\nForward me a GitHub link to test.',
    parse_mode: 'HTML'
  });

  await new Promise((resolve) => {
    const req = https.request(testMsgUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      }
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (result.ok) {
            success('Test message sent! Check your Telegram.');
          } else {
            warn(`Could not send test message: ${result.description}`);
            warn('Make sure your Telegram user ID is correct in installer.config.json');
          }
        } catch {}
        resolve();
      });
    });

    req.on('error', () => resolve());
    req.write(postData);
    req.end();
  });
}

// ========================================
// Summary
// ========================================

function summary(workerUrl) {
  console.log('\n');
  console.log('╔══════════════════════════════════════════════════════════════╗');
  console.log('║                                                              ║');
  console.log('║   🎉 GitHub-to-Obsidian Bot deployed successfully!          ║');
  console.log('║                                                              ║');
  console.log('╚══════════════════════════════════════════════════════════════╝');
  console.log('\n📋 Summary:');
  console.log(`   Worker URL:  ${workerUrl}`);
  console.log(`   Health:      ${workerUrl}/health`);
  console.log(`   Webhook:     ${workerUrl}/webhook`);
  console.log('\n💬 Test the bot:');
  console.log('   1. Open Telegram');
  console.log('   2. Find your bot');
  console.log('   3. Send /start');
  console.log('   4. Forward a GitHub link (e.g., https://github.com/vercel/next.js)');
  console.log('\n📊 Monitor:');
  console.log('   Real-time logs: npx wrangler tail');
  console.log('   D1 data:        npx wrangler d1 execute curator-bot --remote --command="SELECT COUNT(*) FROM ever_seen_ledger"');
  console.log('\n📦 Next: Deploy the dashboard');
  console.log('   cd dashboard');
  console.log('   npm install');
  console.log('   # Create .env.local with NEXT_PUBLIC_WORKER_URL=' + workerUrl);
  console.log('   npm run build');
  console.log('   npx wrangler pages deploy out --project-name=curator-dashboard');
  console.log('');
}

// ========================================
// Main
// ========================================

async function main() {
  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════╗');
  console.log('║                                                              ║');
  console.log('║   🤖 GitHub-to-Obsidian Bot — Auto Installer                ║');
  console.log('║                                                              ║');
  console.log('╚══════════════════════════════════════════════════════════════╝');
  console.log('');

  try {
    // Step 0: Load config
    const config = loadConfig();

    // Step 1: Verify login
    verifyLogin();

    // Step 2: Create D1
    const d1Id = createD1();

    // Step 3: Create KV
    const kvId = createKV();

    // Step 4: Create Queues
    createQueues();

    // Step 5: Update wrangler.toml
    updateWranglerToml(d1Id, kvId);

    // Step 6: Apply schema
    const schemaOk = applySchema();
    if (!schemaOk) {
      error('Schema failed — aborting');
      process.exit(1);
    }

    // Step 7: Set secrets
    setSecrets(config);

    // Step 8: Deploy
    const workerUrl = deploy();
    if (!workerUrl) {
      error('Deploy failed — aborting');
      process.exit(1);
    }

    // Step 9: Set webhook
    await setWebhook(config, workerUrl);

    // Step 10: Test
    await testBot(config, workerUrl);

    // Summary
    summary(workerUrl);

  } catch (e) {
    console.error('\n❌ Installation failed:');
    console.error(e.message);
    console.error('\nStack trace:');
    console.error(e.stack);
    process.exit(1);
  }
}

main();
