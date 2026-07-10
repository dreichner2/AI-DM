#!/usr/bin/env node

const { spawn } = require('node:child_process')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { chromium, expect } = require('@playwright/test')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
const FRONTEND_ROOT = path.resolve(__dirname, '..')
const PYTHON = process.env.PYTHON || path.join(REPO_ROOT, '.venv', 'bin', 'python')
const CHROMIUM_CHANNEL = process.env.PLAYWRIGHT_CHROMIUM_CHANNEL || ''
const SMOKE_TIMEOUT_MS = Number(process.env.AIDM_BROWSER_SMOKE_TIMEOUT_MS || 90_000)
const SMOKE_TOTAL_TIMEOUT_MS = Number(process.env.AIDM_BROWSER_SMOKE_TOTAL_TIMEOUT_MS || 480_000)
const SHUTDOWN_GRACE_MS = Number(process.env.AIDM_BROWSER_SMOKE_SHUTDOWN_GRACE_MS || 2_000)
const SINGLE_ORIGIN_BUILD = process.env.AIDM_BROWSER_SMOKE_SINGLE_ORIGIN === 'true'
const FRONTEND_DIST_DIR = path.join(FRONTEND_ROOT, 'dist')
const FRONTEND_DIST_INDEX = path.join(FRONTEND_DIST_DIR, 'index.html')
const REQUIRED_SECURITY_HEADERS = [
  'Content-Security-Policy',
  'X-Frame-Options',
  'X-Content-Type-Options',
  'Referrer-Policy',
  'Permissions-Policy',
]
const REQUIRED_UI_CSP = {
  'default-src': ["'self'"],
  'script-src': ["'self'"],
  'style-src': ["'self'", 'https://fonts.googleapis.com'],
  'img-src': ["'self'"],
  'font-src': ["'self'", 'https://fonts.gstatic.com'],
  'connect-src': ["'self'", 'ws:'],
  'object-src': ["'none'"],
  'base-uri': ["'self'"],
  'frame-ancestors': ["'none'"],
}

const children = new Set()
let smokeTempDir = null

function log(message) {
  process.stdout.write(`[browser-smoke] ${message}\n`)
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : null
      server.close(() => {
        if (port) resolve(port)
        else reject(new Error('Could not allocate a local port.'))
      })
    })
  })
}

function spawnManaged(command, args, options) {
  const child = spawn(command, args, {
    ...options,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  children.add(child)
  child.stdout.on('data', (chunk) => process.stdout.write(chunk))
  child.stderr.on('data', (chunk) => process.stderr.write(chunk))
  child.once('exit', () => children.delete(child))
  return child
}

function stopManaged(child, signal = 'SIGTERM') {
  if (!child || child.killed) return
  try {
    if (process.platform === 'win32') {
      child.kill(signal)
    } else {
      process.kill(-child.pid, signal)
    }
  } catch {
    child.kill(signal)
  }
}

function cleanupTempDir() {
  if (!smokeTempDir) return
  fs.rmSync(smokeTempDir, { recursive: true, force: true })
  smokeTempDir = null
}

function isPathLikeExecutable(command) {
  return path.isAbsolute(command) || command.includes('/') || command.includes('\\')
}

function waitForChildExit(child, timeoutMs) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}

async function stopAllManaged() {
  const activeChildren = [...children]
  for (const child of activeChildren) {
    stopManaged(child)
  }
  await Promise.all(activeChildren.map((child) => waitForChildExit(child, SHUTDOWN_GRACE_MS)))
  for (const child of [...children]) {
    stopManaged(child, 'SIGKILL')
  }
  await Promise.all([...children].map((child) => waitForChildExit(child, SHUTDOWN_GRACE_MS)))
}

async function shutdown() {
  await stopAllManaged()
  cleanupTempDir()
}

process.once('exit', () => {
  for (const child of [...children]) {
    stopManaged(child)
  }
  cleanupTempDir()
})
process.once('SIGINT', async () => {
  await shutdown()
  process.exit(130)
})
process.once('SIGTERM', async () => {
  await shutdown()
  process.exit(143)
})

async function waitForHttp(url, label) {
  const startedAt = Date.now()
  let lastError = ''
  while (Date.now() - startedAt < SMOKE_TIMEOUT_MS) {
    try {
      const response = await fetch(url)
      if (response.ok) return response
      lastError = `${response.status} ${response.statusText}`
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error)
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for ${label}: ${lastError}`)
}

function parseCsp(csp) {
  const directives = new Map()
  for (const rawDirective of csp.split(';')) {
    const tokens = rawDirective.trim().split(/\s+/).filter(Boolean)
    const [directive, ...values] = tokens
    if (!directive) continue
    directives.set(directive.toLowerCase(), values)
  }
  return directives
}

function assertCspDirectives(response, label) {
  const csp = response.headers.get('Content-Security-Policy') || ''
  const directives = parseCsp(csp)
  const missing = []
  for (const [directive, requiredValues] of Object.entries(REQUIRED_UI_CSP)) {
    const values = directives.get(directive) || []
    for (const requiredValue of requiredValues) {
      if (!values.includes(requiredValue)) {
        missing.push(`${directive} ${requiredValue}`)
      }
    }
  }
  if (missing.length) {
    throw new Error(`${label} Content-Security-Policy is missing directives: ${missing.join(', ')}`)
  }
}

function assertSecurityHeaders(response, label, options = {}) {
  const missing = REQUIRED_SECURITY_HEADERS.filter((header) => !response.headers.get(header))
  if (missing.length) {
    throw new Error(`${label} missing security headers: ${missing.join(', ')}`)
  }
  if (options.requireUiCsp) {
    assertCspDirectives(response, label)
  }
}

async function waitForSessionLog(baseUrl, sessionId, predicate) {
  const startedAt = Date.now()
  let lastPayload = null
  while (Date.now() - startedAt < 30_000) {
    const response = await fetch(`${baseUrl}/api/sessions/${sessionId}/log?limit=200`)
    if (response.ok) {
      const payload = await response.json()
      lastPayload = payload
      const match = predicate(payload.entries || [])
      if (match) return match
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`Timed out waiting for session log update: ${JSON.stringify(lastPayload)}`)
}

async function waitForRouteIds(page) {
  const startedAt = Date.now()
  let lastUrl = ''
  let lastPlayerId = ''
  while (Date.now() - startedAt < 20_000) {
    lastUrl = page.url()
    const url = new URL(lastUrl)
    const campaignId = Number(url.searchParams.get('campaign'))
    const sessionId = Number(url.searchParams.get('session'))
    lastPlayerId = await page.evaluate(
      () => localStorage.getItem('aidm:open:selectedPlayerId') || localStorage.getItem('aidm:selectedPlayerId') || '',
    )
    const playerId = Number(url.searchParams.get('player') || lastPlayerId)
    if (campaignId > 0 && sessionId > 0 && playerId > 0) {
      return { campaignId, sessionId, playerId }
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`Timed out waiting for selected route IDs: ${lastUrl} player=${lastPlayerId || 'none'}`)
}

async function runBrowserFlow(frontendUrl, backendUrl) {
  const browser = await chromium.launch(CHROMIUM_CHANNEL ? { channel: CHROMIUM_CHANNEL } : {})
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []

  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    const sourceUrl = message.location().url || ''
    const isWerkzeugSocketUpgradeNoise =
      text.includes('/socket.io/') &&
      text.includes('WebSocket connection') &&
      (text.includes('Invalid frame header') || text.includes('Data frame received after close'))
    const isExpectedCampaignCommentaryMiss =
      text.includes('404') &&
      sourceUrl.includes('/api/sessions/') &&
      sourceUrl.includes('/campaign-pack/commentary')
    if (!isWerkzeugSocketUpgradeNoise && !isExpectedCampaignCommentaryMiss) consoleErrors.push(text)
  })
  page.on('pageerror', (error) => {
    consoleErrors.push(error.message)
  })

  try {
    await page.goto(frontendUrl, { waitUntil: 'domcontentloaded' })
    await page.locator('.prototype-shell').waitFor({ state: 'visible', timeout: 20_000 })

    await expect(page).toHaveTitle(/AI-DM/)
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)

    await expect(page.getByRole('heading', { name: 'AI-DM' })).toBeVisible()

    const playNowPage = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    playNowPage.on('pageerror', (error) => {
      consoleErrors.push(`Play Now: ${error.message}`)
    })
    await playNowPage.goto(frontendUrl, { waitUntil: 'domcontentloaded' })
    await playNowPage.locator('.prototype-shell').waitFor({ state: 'visible', timeout: 20_000 })
    await playNowPage.getByRole('button', { name: 'Play Now', exact: true }).click()
    await expect(playNowPage.getByLabel(/Your Action/i)).toBeVisible({ timeout: 30_000 })
    await waitForRouteIds(playNowPage)
    await playNowPage.close()

    await page.getByRole('button', { name: 'New Campaign' }).click()
    const createCampaignDialog = page.getByRole('dialog', { name: 'Create New Campaign' })
    await expect(createCampaignDialog).toBeVisible()
    await createCampaignDialog.getByLabel('Campaign Name').fill('Browser Smoke Campaign')
    await createCampaignDialog.getByLabel('Description').fill('Created through the browser smoke UI.')
    await createCampaignDialog.getByLabel('New World Name').fill('Browser Smoke World')
    await createCampaignDialog.getByRole('button', { name: 'Create Campaign', exact: true }).click()
    await expect(createCampaignDialog).toBeHidden({ timeout: 15_000 })
    await expect(page.locator('.session-header').getByText('Browser Smoke Campaign')).toBeVisible()

  const joinCampaignDialog = page.locator('.character-join-dialog')
  const joinCampaignDialogVisible = await joinCampaignDialog
    .waitFor({ state: 'visible', timeout: 10_000 })
    .then(() => true)
    .catch(() => false)
  if (joinCampaignDialogVisible) {
    await joinCampaignDialog.getByRole('button', { name: 'Create Character' }).click()
  }

  const createCharacterDialog = page.locator('.player-edit-dialog')
  const createCharacterDialogVisible = await createCharacterDialog
    .waitFor({ state: 'visible', timeout: 10_000 })
    .then(() => true)
    .catch(() => false)
  if (createCharacterDialogVisible) {
    await createCharacterDialog.getByLabel('Character Name').fill('Browser Smoke Hero')
    const raceSelector = createCharacterDialog.locator('.race-selector')
    await raceSelector.getByLabel('Search races').fill('Human')
    await raceSelector.getByRole('button', { name: 'View Human details' }).click()
    const humanDetailsDialog = page.getByRole('dialog', { name: 'Human' })
    await expect(humanDetailsDialog).toBeVisible()
    await humanDetailsDialog.getByRole('button', { name: 'Select Human' }).click()
    await createCharacterDialog.getByRole('button', { name: 'Preview Wizard class' }).click()
    const wizardDetailsDialog = page.getByRole('dialog', { name: 'Wizard' })
    await expect(wizardDetailsDialog).toBeVisible()
    await wizardDetailsDialog.getByRole('button', { name: 'Select Wizard - Evoker' }).click()
    await createCharacterDialog.getByRole('button', { name: 'Create Character' }).click()
    await expect(createCharacterDialog).toBeHidden({ timeout: 15_000 })
    await expect(page.locator('.right-inspector').getByRole('heading', { name: 'Browser Smoke Hero' })).toBeVisible({ timeout: 15_000 })
  } else {
    await page.getByRole('button', { name: 'Create player' }).click()
    await expect(page.locator('.right-inspector').getByText('New Adventurer')).toBeVisible({ timeout: 15_000 })
  }

  await page.locator('.empty-action-card').getByRole('button', { name: 'Start session' }).click()
  await expect(page.getByRole('heading', { name: /Session \d+/i })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.session-list .session-card.active')).toContainText('Session')
  await expect(page.getByLabel(/Your Action/i)).toBeVisible()
  await expect(page.locator('.composer-tools').getByRole('button', { name: /Roll/i })).toBeVisible()
  const selectedIds = await waitForRouteIds(page)

  await page.locator('.composer-tools').getByRole('button', { name: /Roll/i }).click()
  await expect(page.getByLabel('Roll options')).toBeVisible()
  await page.getByLabel('Roll options').getByRole('button', { name: 'Roll dice' }).click()
  const diceDialog = page.getByRole('dialog', { name: /Rolling dice|Landed|Sending roll/i })
  await expect(diceDialog).toBeVisible({ timeout: 15_000 })
  await expect(diceDialog).toContainText('D20')
  await diceDialog.getByRole('button', { name: 'Close dice roller' }).click()
  await expect(diceDialog).toBeHidden()
  await page.locator('.composer-tools').getByRole('button', { name: /Roll/i }).click()
  await expect(page.getByLabel(/Your Action/i)).toBeVisible()

  const inspectorTabs = page.getByRole('tablist', { name: 'Inspector panels' })
  await inspectorTabs.getByRole('tab', { name: 'Map' }).click()
  await page.getByLabel('Map title').fill('Browser Smoke Map')
  await page.getByLabel('Map description').fill('Smoke route and gatehouse notes.')
  await page.getByRole('button', { name: 'Create map details' }).click()
  await expect(page.locator('.map-segment').getByText('Browser Smoke Map')).toBeVisible({ timeout: 15_000 })
  await page.getByLabel('Segment title').fill('Smoke Gate')
  await page.getByLabel('Segment description').fill('The gatehouse is the active approach.')
  await page.getByLabel('Trigger condition').fill('When the party crosses the smoke line.')
  await page.getByLabel('Tags', { exact: true }).fill('gate, smoke')
  await page.getByRole('button', { name: 'Add segment' }).click()
  await expect(page.locator('.segment-list').getByText('Smoke Gate')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.map-meta-column').getByText(/Smoke Gate/)).toBeVisible()

  const ttsOnButton = page.getByRole('button', { name: 'Turn TTS on' })
  if (await ttsOnButton.isVisible()) {
    await ttsOnButton.click()
    await expect(page.locator('.rail-error-history li p')).toContainText('Deepgram TTS is not configured')
  }

  const actionInput = page.getByLabel(/Your Action/i)
  await actionInput.fill('I inspect the smoke-lit archway.')
  await expect(actionInput).toHaveValue('I inspect the smoke-lit archway.')
  const sendButton = page.locator('.send-button')
  await expect(sendButton).toBeEnabled()
  await sendButton.click()
  const dmEntry = await waitForSessionLog(backendUrl, selectedIds.sessionId, (entries) =>
    entries.find(
      (entry) =>
        entry.entry_type === 'dm' &&
        typeof entry.message === 'string' &&
        entry.metadata?.action_intent?.text === 'I inspect the smoke-lit archway.',
    ),
  )
  await page.getByRole('tab', { name: 'DM Response' }).click()
  const renderedDmText = dmEntry.message.replace(/^DM:\s*/i, '')
  await expect(page.locator('.dm-response-card.expanded .response-copy')).toContainText(
    renderedDmText.slice(0, 48),
    { timeout: 15_000 },
  )

  await page.getByRole('button', { name: 'Session menu' }).click()
  await page.getByRole('menuitem', { name: 'Delete session' }).click()
  await expect(page.getByRole('dialog', { name: 'Delete Session' })).toBeVisible()
  await page.getByRole('button', { name: 'Delete Session' }).click()
  await expect(page.getByRole('heading', { name: /No session selected/i })).toBeVisible({ timeout: 15_000 })

  const importPath = path.join(smokeTempDir, 'browser-smoke-session-import.json')
  fs.writeFileSync(
    importPath,
    JSON.stringify(
      {
        exportedAt: new Date().toISOString(),
        selectedIds: {
          campaignId: selectedIds.campaignId,
          sessionId: selectedIds.sessionId,
          playerId: selectedIds.playerId,
        },
        selectedSession: {
          display_name: 'Imported Smoke Session',
          state_snapshot: {},
        },
        sessionState: {
          current_location: 'Imported Smoke Hall',
          current_quest: 'Verify import flow',
          rolling_summary: 'The browser smoke restored this session from JSON.',
          active_segments: [],
          memory_snippets: [],
        },
        logEntries: [
          {
            message: 'Imported smoke log entry',
            entry_type: 'dm',
            metadata: { source: 'browser-smoke' },
            timestamp: new Date().toISOString(),
          },
        ],
        turnEvents: [],
      },
      null,
      2,
    ),
  )
  await page.getByLabel('Import session file').setInputFiles(importPath)
  await expect(page.getByRole('heading', { name: /Imported Smoke Session/i })).toBeVisible({
    timeout: 15_000,
  })
  const importedIds = await waitForRouteIds(page)
  await waitForSessionLog(backendUrl, importedIds.sessionId, (entries) =>
    entries.find(
      (entry) =>
        entry.entry_type === 'dm' &&
        typeof entry.message === 'string' &&
        entry.message.includes('Imported smoke log entry'),
    ),
  )
  await expect(page.locator('.turn-feed')).toContainText('Imported smoke log entry', {
    timeout: 15_000,
  })

  await page.getByRole('button', { name: 'Session menu' }).click()
  await page.getByRole('menuitem', { name: 'Delete session' }).click()
  await expect(page.getByRole('dialog', { name: 'Delete Session' })).toBeVisible()
  await page.getByRole('button', { name: 'Delete Session' }).click()
  await expect(page.getByRole('heading', { name: /No session selected/i })).toBeVisible({ timeout: 15_000 })

    if (consoleErrors.length) {
      throw new Error(`Browser console errors: ${consoleErrors.join(' | ')}`)
    }
  } finally {
    await browser.close().catch(() => {})
  }
}

function withTimeout(promise, timeoutMs, label) {
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s`))
    }, timeoutMs)
    timer.unref?.()
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

async function main() {
  if (isPathLikeExecutable(PYTHON) && !fs.existsSync(PYTHON)) {
    throw new Error(`Missing Python executable: ${PYTHON}`)
  }

  const backendPort = await getFreePort()
  const frontendPort = await getFreePort()
  const backendUrl = `http://127.0.0.1:${backendPort}`
  const frontendUrl = `http://127.0.0.1:${frontendPort}`
  const appUrl = SINGLE_ORIGIN_BUILD ? backendUrl : frontendUrl
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aidm-browser-smoke-'))
  smokeTempDir = tempDir
  const dbPath = path.join(tempDir, 'browser-smoke.sqlite')

  if (SINGLE_ORIGIN_BUILD && !fs.existsSync(FRONTEND_DIST_INDEX)) {
    throw new Error(`Single-origin browser smoke requires a built frontend at ${FRONTEND_DIST_INDEX}. Run npm run build first.`)
  }

  log(`starting isolated backend on ${backendUrl}`)
  const backend = spawnManaged(
    PYTHON,
    ['-m', 'aidm_server.deploy_bootstrap', '--host', '127.0.0.1', '--port', String(backendPort)],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: REPO_ROOT,
        FLASK_APP: 'aidm_server.main:create_app',
        AIDM_ENV: 'test',
        AIDM_DEBUG: 'false',
        AIDM_DATABASE_URI: `sqlite:///${dbPath}`,
        AIDM_AUTO_CREATE_SCHEMA: 'true',
        AIDM_LLM_PROVIDER: 'fallback',
        AIDM_LLM_MODEL: 'deterministic-v1',
        AIDM_LLM_FALLBACK_MODELS: '',
        AIDM_AUTH_REQUIRED: 'false',
        AIDM_TELEMETRY_ENABLED: 'false',
        AIDM_SECURITY_HEADERS_ENABLED: 'true',
        AIDM_SERVE_FRONTEND: SINGLE_ORIGIN_BUILD ? 'true' : 'false',
        AIDM_FRONTEND_DIST_DIR: FRONTEND_DIST_DIR,
        AIDM_SOCKETIO_ASYNC_MODE: 'threading',
        AIDM_CORS_ALLOWLIST: SINGLE_ORIGIN_BUILD ? backendUrl : frontendUrl,
        AIDM_SOCKET_CORS_ALLOWLIST: SINGLE_ORIGIN_BUILD ? backendUrl : frontendUrl,
      },
    },
  )
  const healthResponse = await waitForHttp(`${backendUrl}/api/health`, 'backend health')
  assertSecurityHeaders(healthResponse, 'backend health')

  if (SINGLE_ORIGIN_BUILD) {
    const frontendResponse = await waitForHttp(`${backendUrl}/`, 'single-origin frontend')
    assertSecurityHeaders(frontendResponse, 'single-origin frontend', { requireUiCsp: true })
    log(`using built single-origin frontend from ${FRONTEND_DIST_DIR}`)
  } else {
    log(`starting frontend on ${frontendUrl}`)
    spawnManaged(
      'npm',
      ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(frontendPort), '--strictPort'],
      {
        cwd: FRONTEND_ROOT,
        env: {
          ...process.env,
          VITE_AIDM_API_BASE_URL: backendUrl,
        },
      },
    )
    await waitForHttp(frontendUrl, 'frontend dev server')
  }

  log('running browser flow')
  await runBrowserFlow(appUrl, backendUrl)

  await stopAllManaged()
  cleanupTempDir()
  log('passed: create campaign -> create player -> start session -> manage map/segments -> toggle TTS unavailable state -> send action -> receive DM response -> delete session -> import session -> delete imported session')
}

withTimeout(main(), SMOKE_TOTAL_TIMEOUT_MS, 'Browser smoke')
  .catch(async (error) => {
    await shutdown()
    console.error(`[browser-smoke][error] ${error instanceof Error ? error.message : String(error)}`)
    process.exit(1)
  })
