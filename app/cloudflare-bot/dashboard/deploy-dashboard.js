#!/usr/bin/env node
/**
 * deploy-dashboard.js — Auto-deploy the dashboard to Cloudflare Pages
 * ================================================================
 * Reads the Worker URL, builds the Next.js dashboard, deploys to Pages.
 *
 * Prerequisites:
 *   - Node.js 18+
 *   - Run from the dashboard/ folder
 *   - npm install already done
 *   - npx wrangler login already done
 *
 * Usage:
 *   node deploy-dashboard.js
 *   node deploy-dashboard.js --worker-url https://github-to-obsidian-bot.your-subdomain.workers.dev
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ========================================
// Helpers
// ========================================

function log(msg) { console.log(msg); }
function success(msg) { console.log(`  ✅ ${msg}`); }
function info(msg) { console.log(`  ℹ️  ${msg}`); }
function warn(msg) { console.log(`  ⚠️  ${msg}`); }
function error(msg) { console.error(`  ❌ ${msg}`); }

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
      timeout: 300000,  // 5 min timeout for builds
      ...opts
    });
  } catch (e) {
    if (opts.silent) {
      const stdout = e.stdout ? e.stdout.toString() : '';
      const stderr = e.stderr ? e.stderr.toString() : '';
      return stdout + stderr;
    }
    throw e;
  }
}

// ========================================
// Get Worker URL
// ========================================

function getWorkerUrl() {
  // Check command line arg
  const argIdx = process.argv.indexOf('--worker-url');
  if (argIdx >= 0 && process.argv[argIdx + 1]) {
    return normalizeUrl(process.argv[argIdx + 1]);
  }

  // Check .env.local
  const envPath = path.join(__dirname, '.env.local');
  if (fs.existsSync(envPath)) {
    const content = fs.readFileSync(envPath, 'utf8');
    const match = content.match(/NEXT_PUBLIC_WORKER_URL\s*=\s*(.+)/);
    if (match) {
      return normalizeUrl(match[1]);
    }
  }

  // Prompt user
  console.log('\n📋 Enter your Worker URL');
  console.log('   (from the Worker deployment — looks like:');
  console.log('    https://github-to-obsidian-bot.YOUR-SUBDOMAIN.workers.dev)\n');

  const readline = require('readline').createInterface({
    input: process.stdin,
    output: process.stdout
  });

  return new Promise((resolve) => {
    readline.question('   Worker URL: ', (url) => {
      readline.close();
      resolve(normalizeUrl(url));
    });
  });
}

function normalizeUrl(url) {
  if (!url) return '';
  url = url.trim().replace(/\/+$/, '').replace(/['"]/g, '');
  // Add https:// if missing
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url;
  }
  return url;
}

// ========================================
// Main
// ========================================

async function main() {
  console.log('');
  console.log('╔══════════════════════════════════════════════════════════════╗');
  console.log('║                                                              ║');
  console.log('║   📊 Dashboard Auto-Deployer                                ║');
  console.log('║   GitHub-to-Obsidian Bot — Cloudflare Pages                 ║');
  console.log('║                                                              ║');
  console.log('╚══════════════════════════════════════════════════════════════╝');
  console.log('');

  try {
    // Step 1: Get Worker URL
    step(1, 'Getting Worker URL');
    const workerUrl = await getWorkerUrl();

    if (!workerUrl || !workerUrl.includes('workers.dev')) {
      error('Invalid Worker URL. Must be a *.workers.dev URL.');
      error(`Got: ${workerUrl}`);
      process.exit(1);
    }

    success(`Worker URL: ${workerUrl}`);

    // Step 2: Create .env.local
    step(2, 'Creating .env.local');
    const envContent = `NEXT_PUBLIC_WORKER_URL=${workerUrl}\n`;
    fs.writeFileSync(path.join(__dirname, '.env.local'), envContent);
    success('.env.local created');

    // Step 3: Install dependencies
    step(3, 'Installing dependencies');
    if (!fs.existsSync(path.join(__dirname, 'node_modules'))) {
      info('Running npm install...');
      const installOutput = run('npm install', { silent: true });
      success('Dependencies installed');
    } else {
      success('node_modules exists — skipping install');
    }

    // Step 4: Build
    step(4, 'Building dashboard (static export)');
    info('Running npm run build...');
    const buildOutput = run('npm run build', { silent: true });

    // Check if out/ folder was created
    const outDir = path.join(__dirname, 'out');
    if (!fs.existsSync(outDir)) {
      error('Build failed — out/ folder not created');
      console.log(buildOutput);
      process.exit(1);
    }

    const fileCount = fs.readdirSync(outDir, { recursive: true }).length;
    success(`Build complete — ${fileCount} files in out/`);

    // Step 5: Deploy to Cloudflare Pages
    step(5, 'Deploying to Cloudflare Pages');

    // First, create the project if it doesn't exist
    info('Checking if Pages project exists...');
    const listOutput = run('npx wrangler pages project list', { silent: true });

    if (!listOutput.includes('curator-dashboard')) {
      info('Creating Pages project "curator-dashboard"...');
      const createOutput = run('npx wrangler pages project create curator-dashboard --production-branch=main', { silent: true });
      console.log(createOutput);
      success('Pages project created');
    } else {
      success('Pages project already exists');
    }

    // Now deploy
    info('Deploying to Pages...');
    const deployOutput = run('npx wrangler pages deploy out --project-name=curator-dashboard', { silent: true });
    console.log(deployOutput);

    // Extract dashboard URL
    const urlMatch = deployOutput.match(/https:\/\/[a-z0-9-]+\.pages\.dev/);
    if (urlMatch) {
      const dashboardUrl = urlMatch[0];
      success(`Dashboard deployed: ${dashboardUrl}`);

      // Step 6: Test
      step(6, 'Next steps');
      console.log('');
      console.log('╔══════════════════════════════════════════════════════════════╗');
      console.log('║                                                              ║');
      console.log('║   🎉 Dashboard deployed successfully!                        ║');
      console.log('║                                                              ║');
      console.log('╚══════════════════════════════════════════════════════════════╝');
      console.log('');
      console.log(`📊 Dashboard URL: ${dashboardUrl}`);
      console.log('');
      console.log('💬 How to log in:');
      console.log('   1. Open the dashboard URL in your browser');
      console.log('   2. Enter your Telegram user ID (123456789)');
      console.log('   3. Click "Send Magic Link"');
      console.log('   4. Check your Telegram — bot DMs you a login link');
      console.log('   5. Click the link → you\'re logged in!');
      console.log('');
      console.log('🖥️  In the desktop app:');
      console.log(`   Go to ☁️ Cloudflare tab → Worker URL field → paste:`);
      console.log(`   ${workerUrl}`);
      console.log(`   Then click 📊 Dashboard button in the toolbar.`);
      console.log('');
      console.log('📋 Summary:');
      console.log(`   Worker URL:    ${workerUrl}`);
      console.log(`   Dashboard URL: ${dashboardUrl}`);
      console.log(`   Login:         Magic link via Telegram bot`);
      console.log('');
    } else {
      warn('Could not extract dashboard URL from deploy output');
      warn('Check the output above for the URL.');
    }

  } catch (e) {
    console.error('\n❌ Deployment failed:');
    console.error(e.message);
    if (e.stack) {
      console.error('\nStack trace:');
      console.error(e.stack);
    }
    process.exit(1);
  }
}

main();
