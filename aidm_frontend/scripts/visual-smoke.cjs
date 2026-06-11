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
const SMOKE_TIMEOUT_MS = Number(process.env.AIDM_VISUAL_SMOKE_TIMEOUT_MS || 90_000)
const ARTIFACT_ROOT = path.join(REPO_ROOT, 'tmp', 'verification_artifacts', 'visual-smoke')

const children = new Set()
let smokeTempDir = null

const VIEWPORTS = [
  { name: 'desktop-shell', width: 1440, height: 900, fullPage: false },
  { name: 'short-height-composer', width: 1280, height: 620, fullPage: false },
  { name: 'mobile-full', width: 390, height: 844, fullPage: true },
]

function log(message) {
  process.stdout.write(`[visual-smoke] ${message}\n`)
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

function stopManaged(child) {
  if (!child || child.killed) return
  try {
    if (process.platform === 'win32') {
      child.kill('SIGTERM')
    } else {
      process.kill(-child.pid, 'SIGTERM')
    }
  } catch {
    child.kill('SIGTERM')
  }
}

function cleanupTempDir() {
  if (!smokeTempDir) return
  fs.rmSync(smokeTempDir, { recursive: true, force: true })
  smokeTempDir = null
}

async function shutdown() {
  for (const child of [...children]) {
    stopManaged(child)
  }
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

async function postJson(baseUrl, pathName, payload) {
  return writeJson(baseUrl, pathName, payload, 'POST')
}

async function patchJson(baseUrl, pathName, payload) {
  return writeJson(baseUrl, pathName, payload, 'PATCH')
}

async function writeJson(baseUrl, pathName, payload, method) {
  const response = await fetch(`${baseUrl}${pathName}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const text = await response.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!response.ok) {
    throw new Error(`${pathName} failed with ${response.status}: ${text}`)
  }
  return body
}

async function seedWorkspace(baseUrl) {
  const world = await postJson(baseUrl, '/api/worlds', {
    name: 'Visual Smoke World',
    description: 'Isolated visual smoke world.',
  })
  const campaign = await postJson(baseUrl, '/api/campaigns', {
    title: 'Visual Smoke Campaign',
    description: 'Created by the visual smoke test.',
    world_id: world.world_id,
  })
  const player = await postJson(baseUrl, `/api/players/campaigns/${campaign.campaign_id}/players`, {
    name: 'Visual Player',
    character_name: 'Vista Ember',
    char_class: 'Wizard',
    race: 'Human',
    level: 2,
  })
  const session = await postJson(baseUrl, '/api/sessions/start', {
    campaign_id: campaign.campaign_id,
  })
  const renamedSession = await patchJson(baseUrl, `/api/sessions/${session.session_id}`, {
    name: 'Visual Smoke Session',
  })
  return { world, campaign, player, session: renamedSession }
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

async function assertLayoutHealth(page, viewport) {
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await expect(page.locator('.prototype-shell')).toBeVisible()
  await expect(page.locator('.ops-bar')).toBeVisible()
  await expect(page.locator('.turn-feed')).toBeVisible()
  await expect(page.locator('.action-composer')).toBeVisible()
  await expect(page.locator('.composer-tools')).toBeVisible()
  await expect(page.locator('.right-inspector')).toBeVisible()

  const metrics = await page.evaluate(() => {
    const selectorList = [
      '.prototype-shell',
      '.ops-bar',
      '.turn-feed',
      '.action-composer',
      '.composer-tools',
      '.right-inspector',
    ]
    const boxes = {}
    for (const selector of selectorList) {
      const element = document.querySelector(selector)
      if (!element) continue
      const rect = element.getBoundingClientRect()
      boxes[selector] = {
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      }
    }
    return {
      boxes,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
      },
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }
  })

  const overflowX = metrics.scrollWidth - metrics.clientWidth
  if (overflowX > 6) {
    throw new Error(`${viewport.name} has horizontal overflow of ${overflowX}px`)
  }

  if (viewport.width >= 981) {
    const topBar = metrics.boxes['.ops-bar']
    const composerTools = metrics.boxes['.composer-tools']
    const actionComposer = metrics.boxes['.action-composer']
    const inspector = metrics.boxes['.right-inspector']
    if (!topBar || !composerTools || !actionComposer || !inspector) {
      throw new Error(`${viewport.name} is missing required desktop layout boxes`)
    }
    if (topBar.top < -1 || topBar.bottom > metrics.viewport.height + 1) {
      throw new Error(`${viewport.name} top bar is clipped`)
    }
    if (composerTools.bottom > metrics.viewport.height - 4) {
      throw new Error(`${viewport.name} composer tools are too close to the viewport bottom`)
    }
    if (actionComposer.bottom > metrics.viewport.height + 1) {
      throw new Error(`${viewport.name} action composer is clipped below the viewport`)
    }
    if (inspector.right > metrics.viewport.width + 1) {
      throw new Error(`${viewport.name} inspector is clipped horizontally`)
    }
  }
}

async function runVisualFlow(frontendUrl, backendUrl, ids, artifactDir) {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => {
    consoleErrors.push(error.message)
  })

  const routeUrl = `${frontendUrl}/?campaign=${ids.campaign.campaign_id}&session=${ids.session.session_id}&player=${ids.player.player_id}`
  await page.addInitScript((playerId) => {
    localStorage.setItem('aidm:open:selectedPlayerId', String(playerId))
  }, ids.player.player_id)
  await page.goto(routeUrl, { waitUntil: 'domcontentloaded' })
  await page.locator('.prototype-shell').waitFor({ state: 'visible', timeout: 20_000 })
  await expect(page).toHaveTitle(/AI-DM/)
  await expect(page.getByRole('heading', { name: /Visual Smoke Session/i })).toBeVisible()
  await expect(page.locator('.session-header').getByText('Visual Smoke Campaign')).toBeVisible()
  await expect(page.locator('.right-inspector').getByRole('heading', { name: 'Vista Ember' })).toBeVisible()

  await page.getByLabel(/Your Action/i).fill('I scan the balcony for movement.')
  await page.getByRole('button', { name: 'Send' }).click()
  const dmEntry = await waitForSessionLog(backendUrl, ids.session.session_id, (entries) =>
    entries.find(
      (entry) =>
        entry.entry_type === 'dm' &&
        typeof entry.message === 'string' &&
        entry.metadata?.action_intent?.text === 'I scan the balcony for movement.',
    ),
  )
  const renderedDmText = dmEntry.message.replace(/^DM:\s*/i, '')
  await expect(page.locator('.dm-response-card .response-copy')).toContainText(
    renderedDmText.slice(0, 48),
    { timeout: 15_000 },
  )

  const screenshots = []
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await assertLayoutHealth(page, viewport)
    const fileName = `${viewport.name}.png`
    const filePath = path.join(artifactDir, fileName)
    await page.screenshot({ path: filePath, fullPage: viewport.fullPage })
    screenshots.push(filePath)
  }

  await browser.close()
  if (consoleErrors.length) {
    throw new Error(`Browser console errors: ${consoleErrors.join(' | ')}`)
  }
  return screenshots
}

async function main() {
  if (!fs.existsSync(PYTHON)) {
    throw new Error(`Missing Python executable: ${PYTHON}`)
  }

  const backendPort = await getFreePort()
  const frontendPort = await getFreePort()
  const backendUrl = `http://127.0.0.1:${backendPort}`
  const frontendUrl = `http://127.0.0.1:${frontendPort}`
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aidm-visual-smoke-'))
  smokeTempDir = tempDir
  const dbPath = path.join(tempDir, 'visual-smoke.sqlite')
  const artifactDir = path.join(
    ARTIFACT_ROOT,
    new Date().toISOString().replace(/[:.]/g, '-'),
  )
  fs.mkdirSync(artifactDir, { recursive: true })

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
        AIDM_DATABASE_URI: `sqlite:///${dbPath}`,
        AIDM_AUTO_CREATE_SCHEMA: 'true',
        AIDM_LLM_PROVIDER: 'fallback',
        AIDM_LLM_MODEL: 'deterministic-v1',
        AIDM_LLM_FALLBACK_MODELS: '',
        AIDM_AUTH_REQUIRED: 'false',
        AIDM_TELEMETRY_ENABLED: 'false',
        AIDM_SOCKETIO_ASYNC_MODE: 'threading',
        AIDM_CORS_ALLOWLIST: '*',
        AIDM_SOCKET_CORS_ALLOWLIST: '*',
      },
    },
  )
  await waitForHttp(`${backendUrl}/api/health`, 'backend health')

  log('seeding isolated campaign/session/player')
  const ids = await seedWorkspace(backendUrl)

  log(`starting frontend on ${frontendUrl}`)
  const frontend = spawnManaged(
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

  log('capturing visual smoke screenshots')
  const screenshots = await runVisualFlow(frontendUrl, backendUrl, ids, artifactDir)

  stopManaged(frontend)
  stopManaged(backend)
  cleanupTempDir()
  log(`passed: ${screenshots.length} screenshots written under ${path.relative(REPO_ROOT, artifactDir)}`)
}

main()
  .catch(async (error) => {
    await shutdown()
    console.error(`[visual-smoke][error] ${error instanceof Error ? error.message : String(error)}`)
    process.exit(1)
  })
