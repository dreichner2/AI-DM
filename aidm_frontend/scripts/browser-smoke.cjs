#!/usr/bin/env node

const { spawn, spawnSync } = require('node:child_process')
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
// A running single-origin server (built frontend served by the backend) to test instead of starting one.
const SMOKE_BASE_URL = (process.env.AIDM_SMOKE_BASE_URL || '').trim().replace(/\/+$/, '')
const BACKEND_SERVER = (
  process.env.AIDM_BROWSER_SMOKE_BACKEND_SERVER
  || (process.platform === 'win32' ? 'werkzeug' : 'gunicorn')
).trim().toLowerCase()
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

function runToCompletion(command, args, options, label) {
  const result = spawnSync(command, args, {
    ...options,
    stdio: 'inherit',
  })
  if (result.error) {
    throw new Error(`${label} could not start: ${result.error.message}`)
  }
  if (result.status !== 0) {
    const exitDescription = result.signal ? `signal ${result.signal}` : `exit code ${result.status}`
    throw new Error(`${label} failed with ${exitDescription}`)
  }
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

async function postJson(baseUrl, pathName, payload) {
  const response = await fetch(`${baseUrl}${pathName}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const text = await response.text()
  if (!response.ok) throw new Error(`${pathName} failed with ${response.status}: ${text}`)
  return text ? JSON.parse(text) : null
}

async function createBlankCampaign(baseUrl, title, description) {
  const world = await postJson(baseUrl, '/api/worlds', { name: `${title} World`, description })
  return postJson(baseUrl, '/api/campaigns', { title, description, world_id: world.world_id })
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

// The title screen offers Play Now directly to new players; returning players open it first.
async function clickPlayNow(page) {
  const playNow = page.getByRole('button', { name: /^Play now/i })
  if (!(await playNow.isVisible())) {
    await page.getByRole('button', { name: 'Play a ready-made adventure' }).click()
  }
  await playNow.click()
}

async function waitForStoryNarrations(page, count) {
  await page.waitForFunction(
    (minimum) => [...document.querySelectorAll('.story-feed .story-narration:not([data-streaming])')]
      .filter((beat) => (beat.textContent || '').trim()).length >= minimum,
    count,
    { timeout: 60_000 },
  )
}

async function expectNoDeliveryUncertainty(page) {
  await expect(page.getByText('Delivery uncertain')).toHaveCount(0)
  await expect(page.getByText('We could not confirm that reached the table.')).toHaveCount(0)
}

// The Story top bar has two "Adventure menu" buttons (the mark and the menu icon); both open the same menu.
async function openStoryMenuItem(page, name) {
  await page.getByRole('button', { name: 'Adventure menu' }).first().click()
  await page.getByRole('menu', { name: 'Adventure menu' }).getByRole('menuitem', { name }).click()
}

// The hero creator's radios and checkboxes are visually hidden inside their labels, so click the label as a player does.
async function choose(scope, role, name) {
  const control = scope.getByRole(role, { name, exact: true })
  await control.locator('..').click()
  await expect(control).toBeChecked()
}

// The guided hero creation flow: Who, Calling, Shape (class skills), Review, then Begin.
async function createHero(page, heroName) {
  const creator = page.getByRole('dialog', { name: /^A new hero/ })
  await expect(creator).toBeVisible({ timeout: 15_000 })
  const continueButton = creator.getByRole('button', { name: 'Continue', exact: true })
  await creator.getByRole('textbox', { name: 'Hero name' }).fill(heroName)
  await choose(creator.getByRole('group', { name: 'Ancestry' }), 'radio', 'Human')
  await continueButton.click()
  await choose(creator.getByRole('group', { name: 'Calling' }), 'radio', /^Wizard\b/)
  await continueButton.click()
  const classSkills = creator.getByRole('group', { name: /^Class skills/ })
  await choose(classSkills, 'checkbox', 'Insight')
  await choose(classSkills, 'checkbox', 'Medicine')
  await continueButton.click()
  await creator.getByRole('button', { name: `Begin as ${heroName}` }).click()
  await expect(creator).toBeHidden({ timeout: 15_000 })
}

// Story mode is the default play screen: Play Now, the opening, then one action.
async function runStoryFlow(browser, frontendUrl, backendUrl, consoleErrors) {
  const storyPage = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  storyPage.on('pageerror', (error) => {
    consoleErrors.push(`Story: ${error.message}`)
  })
  await storyPage.goto(frontendUrl, { waitUntil: 'domcontentloaded' })
  await storyPage.locator('.prototype-shell').waitFor({ state: 'visible', timeout: 20_000 })
  await expect(storyPage.getByRole('heading', { name: 'AI-DM' })).toBeVisible()
  await clickPlayNow(storyPage)
  await expect(storyPage.locator('.story-shell')).toBeVisible({ timeout: 30_000 })
  await expect(storyPage.locator('.ops-bar')).toHaveCount(0)
  await waitForStoryNarrations(storyPage, 1)
  await expectNoDeliveryUncertainty(storyPage)
  const storyIds = await waitForRouteIds(storyPage)

  const actionInput = storyPage.getByLabel('Your Action')
  await expect(actionInput).toBeEnabled({ timeout: 15_000 })
  await actionInput.fill('I look for whoever scratched the names out of the ledger.')
  // The box unlocks when the engine has finished the opening turn, a moment after its narration.
  await expect(storyPage.getByRole('button', { name: 'Send', exact: true })).toBeEnabled({ timeout: 15_000 })
  await actionInput.press('Enter')
  await waitForStoryNarrations(storyPage, 2)
  await waitForSessionLog(backendUrl, storyIds.sessionId, (entries) =>
    entries.find(
      (entry) =>
        entry.entry_type === 'dm' &&
        entry.metadata?.action_intent?.text === 'I look for whoever scratched the names out of the ledger.',
    ),
  )
  await expectNoDeliveryUncertainty(storyPage)

  await openStoryMenuItem(storyPage, 'Settings')
  const settingsSheet = storyPage.getByRole('dialog', { name: 'Settings' })
  await expect(settingsSheet.getByRole('group', { name: 'Text size' })).toBeVisible()
  await settingsSheet.getByRole('button', { name: 'Close settings' }).click()
  await expect(settingsSheet).toBeHidden()

  await openStoryMenuItem(storyPage, 'Switch adventure')
  const adventuresSheet = storyPage.getByRole('dialog', { name: 'Your adventures' })
  await expect(adventuresSheet.getByText('Here now')).toBeVisible({ timeout: 15_000 })
  await adventuresSheet.getByRole('button', { name: 'Close your adventures' }).click()
  await expect(adventuresSheet).toBeHidden()
  await storyPage.close()
}

async function launchChromium() {
  if (CHROMIUM_CHANNEL) return chromium.launch({ channel: CHROMIUM_CHANNEL })
  try {
    return await chromium.launch()
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes("Executable doesn't exist")) throw error
    log('bundled Chromium is not installed; retrying with the installed Google Chrome (channel "chrome")')
    return chromium.launch({ channel: 'chrome' })
  }
}

async function runBrowserFlow(frontendUrl, backendUrl) {
  const browser = await launchChromium()
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
    // The flows below exercise the classic console ("Table tools").
    await page.addInitScript(() => localStorage.setItem('aidm:playView', 'console'))
    await page.goto(frontendUrl, { waitUntil: 'domcontentloaded' })
    await page.locator('.prototype-shell').waitFor({ state: 'visible', timeout: 20_000 })

    await expect(page).toHaveTitle(/AI-DM/)
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)

    await expect(page.getByRole('heading', { name: 'AI-DM' })).toBeVisible()

    await runStoryFlow(browser, frontendUrl, backendUrl, consoleErrors)

    // The smoke's server narrates with the scripted narrator, which can tell a ready-made adventure but not a
    // blank page: the sheet says so and keeps the blank page closed, so the blank adventure is made through the API.
    await page.getByRole('button', { name: /^(Create your own adventure|New adventure)$/ }).click()
    const newAdventureSheet = page.getByRole('dialog', { name: 'New adventure' })
    await expect(newAdventureSheet).toBeVisible({ timeout: 15_000 })
    await expect(newAdventureSheet.getByText('Needs an AI narrator — choose one in Settings')).toBeVisible()
    await expect(newAdventureSheet.getByRole('button', { name: 'Begin with a blank page' })).toBeDisabled()
    await newAdventureSheet.getByRole('button', { name: 'Close new adventure' }).click()
    await expect(newAdventureSheet).toBeHidden()
    const campaign = await createBlankCampaign(backendUrl, 'Browser Smoke Campaign', 'Created through the browser smoke API.')
    await page.goto(`${frontendUrl}/?campaign=${campaign.campaign_id}`, { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.session-header').getByText('Browser Smoke Campaign')).toBeVisible({ timeout: 15_000 })

  // A new adventure without characters asks who you are playing; New character opens the hero creator.
  const characterChooser = page.getByRole('dialog', { name: 'Choose your character' })
  await expect(characterChooser).toBeVisible({ timeout: 15_000 })
  await characterChooser.getByRole('button', { name: 'New character' }).click()
  await createHero(page, 'Browser Smoke Hero')
  await expect(page.locator('.right-inspector').getByRole('heading', { name: 'Browser Smoke Hero' })).toBeVisible({ timeout: 15_000 })

  await page.locator('.empty-action-card').getByRole('button', { name: 'Start next chapter' }).click()
  const startChapterDialog = page.getByRole('dialog', { name: 'Start the next chapter' })
  await expect(startChapterDialog).toBeVisible()
  await startChapterDialog.getByRole('button', { name: 'Start chapter', exact: true }).click()
  await expect(startChapterDialog).toBeHidden({ timeout: 15_000 })
  await expect(page.getByRole('heading', { name: /(Session|Chapter) \d+/i })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.session-list .session-card.active')).toContainText('Chapter')
  await expect(page.getByLabel(/Your Action/i)).toBeVisible()
  await expect(page.locator('.composer-tools').getByRole('button', { name: /Roll/i })).toBeVisible()
  const selectedIds = await waitForRouteIds(page)

  await page.locator('.composer-tools').getByRole('button', { name: /Roll/i }).click()
  await expect(page.getByLabel('Roll options')).toBeVisible()
  await page.getByLabel('Roll options').getByRole('button', { name: 'Roll dice' }).click()
  // The roll is its roll card where it was pressed: no dialog, no 3D die. It lands on the server's
  // result, then hands over to the turn's cards above the narration.
  const pressedRoll = page.getByRole('region', { name: 'Your roll' })
  await expect(pressedRoll).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('.turn-roll-cards .roll-card').first()).toHaveAttribute('data-phase', 'done', { timeout: 30_000 })
  await expect(pressedRoll).toBeHidden({ timeout: 30_000 })
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

  const unavailableTtsControl = page.getByRole('button', {
    name: 'No narrator voice is set up on this server.',
  })
  await expect(unavailableTtsControl).toBeVisible()
  await expect(unavailableTtsControl).toHaveAttribute('data-tts-configuration', 'unavailable')
  await expect(unavailableTtsControl).toHaveAttribute(
    'title',
    'Unavailable: No narrator voice is set up on this server.',
  )

  const actionInput = page.getByLabel(/Your Action/i)
  await actionInput.fill('I inspect the smoke-lit archway.')
  await expect(actionInput).toHaveValue('I inspect the smoke-lit archway.')
  await expect(page.locator('.status-dot').filter({ hasText: 'Joined' })).toBeVisible({ timeout: 15_000 })
  const sendButton = page.locator('.send-button')
  await expect(sendButton).toBeEnabled()
  await page.evaluate(() => {
    window.aidmStreamingSmokeProbe?.observer?.disconnect()
    const snapshots = []
    const captureStreamingResponse = () => {
      const currentCard = document.querySelector('.turn-row.current .dm-response-card')
      const status = currentCard?.querySelector('.turn-speaker span')?.textContent?.trim() || ''
      const response = currentCard?.querySelector('.response-copy')?.textContent?.trim() || ''
      if (status !== 'Streaming' || !response || response === '...' || snapshots.includes(response)) return
      snapshots.push(response)
    }
    const observer = new MutationObserver(captureStreamingResponse)
    observer.observe(document.body, { childList: true, characterData: true, subtree: true })
    window.aidmStreamingSmokeProbe = { observer, snapshots }
    captureStreamingResponse()
  })
  await sendButton.click()
  try {
    await page.waitForFunction(
      () => (window.aidmStreamingSmokeProbe?.snapshots.length || 0) >= 2,
      undefined,
      { timeout: 15_000 },
    )
  } catch (error) {
    const diagnostics = await page.evaluate(() => {
      const currentCard = document.querySelector('.turn-row.current .dm-response-card')
      const snapshots = [...(window.aidmStreamingSmokeProbe?.snapshots || [])]
      return {
        realtime: [...document.querySelectorAll('.status-dot')]
          .map((element) => element.textContent?.trim() || '')
          .find((label) => ['Joined', 'Connecting', 'Error', 'Offline', 'Standby'].includes(label)) || 'missing',
        status: currentCard?.querySelector('.turn-speaker span')?.textContent?.trim() || 'missing',
        responseLength: currentCard?.querySelector('.response-copy')?.textContent?.trim().length || 0,
        snapshotCount: snapshots.length,
        snapshotLengths: snapshots.map((snapshot) => snapshot.length),
      }
    })
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(
      `Timed out waiting for visible incremental DM streaming (${message}); `
      + `diagnostics=${JSON.stringify({ ...diagnostics, consoleErrors: consoleErrors.slice(-5) })}`,
    )
  }
  const streamingSnapshots = await page.evaluate(() => {
    const snapshots = [...(window.aidmStreamingSmokeProbe?.snapshots || [])]
    window.aidmStreamingSmokeProbe?.observer?.disconnect()
    return snapshots
  })
  if (
    streamingSnapshots.length < 2
    || streamingSnapshots.some((snapshot) => !snapshot.trim())
    || new Set(streamingSnapshots).size < 2
  ) {
    throw new Error('Expected at least two distinct nonempty DM response snapshots while Streaming')
  }
  log(`observed ${streamingSnapshots.length} distinct nonempty DM response snapshots while Streaming`)
  const dmEntry = await waitForSessionLog(backendUrl, selectedIds.sessionId, (entries) =>
    entries.find(
      (entry) =>
        entry.entry_type === 'dm' &&
        typeof entry.message === 'string' &&
        entry.metadata?.action_intent?.text === 'I inspect the smoke-lit archway.',
    ),
  )
  const renderedDmText = dmEntry.message.replace(/^DM:\s*/i, '')
  await expect(page.locator('.turn-row.current .dm-response-card .response-copy')).toContainText(
    renderedDmText.slice(0, 48),
    { timeout: 15_000 },
  )

  await page.getByRole('button', { name: 'Session menu' }).click()
  await page.getByRole('button', { name: 'Delete session' }).click()
  await expect(page.getByRole('dialog', { name: 'Delete this chapter' })).toBeVisible()
  await page.getByRole('button', { name: 'Delete this chapter' }).click()
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
  await page.getByRole('button', { name: 'Delete session' }).click()
  await expect(page.getByRole('dialog', { name: 'Delete this chapter' })).toBeVisible()
  await page.getByRole('button', { name: 'Delete this chapter' }).click()
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

const PASSED_MESSAGE = 'passed: Story Play Now -> opening narration -> one action -> Settings -> Your adventures -> New adventure (blank page closed to the scripted narrator) -> blank adventure via API -> Choose your character -> guided hero creation -> start chapter -> roll a d20 as an inline roll card -> manage map/segments -> verify control-local TTS unavailable state -> observe incremental DM streaming -> receive final DM response -> delete session -> import session -> delete imported session'

async function checkSingleOriginServer(baseUrl) {
  const healthResponse = await waitForHttp(`${baseUrl}/api/health`, 'backend health')
  assertSecurityHeaders(healthResponse, 'backend health')
  const frontendResponse = await waitForHttp(`${baseUrl}/`, 'single-origin frontend')
  assertSecurityHeaders(frontendResponse, 'single-origin frontend', { requireUiCsp: true })
}

async function main() {
  if (SMOKE_BASE_URL) {
    log(`using the running single-origin server at ${SMOKE_BASE_URL}`)
    await checkSingleOriginServer(SMOKE_BASE_URL)
    smokeTempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aidm-browser-smoke-'))
    log('running browser flow')
    await runBrowserFlow(SMOKE_BASE_URL, SMOKE_BASE_URL)
    cleanupTempDir()
    log(PASSED_MESSAGE)
    return
  }
  if (isPathLikeExecutable(PYTHON) && !fs.existsSync(PYTHON)) {
    throw new Error(`Missing Python executable: ${PYTHON}`)
  }
  if (!['gunicorn', 'werkzeug'].includes(BACKEND_SERVER)) {
    throw new Error(
      `Unsupported AIDM_BROWSER_SMOKE_BACKEND_SERVER=${BACKEND_SERVER}; expected gunicorn or werkzeug`,
    )
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

  const backendEnv = {
    ...process.env,
    PYTHONPATH: REPO_ROOT,
    FLASK_APP: 'aidm_server.main:create_app',
    AIDM_ENV: 'test',
    AIDM_SKIP_REPO_ENV_LOCAL: '1',
    AIDM_DEBUG: 'false',
    AIDM_DATABASE_URI: `sqlite:///${dbPath}`,
    AIDM_AUTO_CREATE_SCHEMA: 'false',
    AIDM_LLM_PROVIDER: 'fallback',
    AIDM_LLM_MODEL: 'deterministic-v1',
    AIDM_LLM_FALLBACK_MODELS: '',
    AIDM_BUFFERED_STREAM_CHUNK_CHARS: '48',
    AIDM_BUFFERED_STREAM_CHUNK_DELAY_MS: '100',
    AIDM_DEEPGRAM_API_KEY: '',
    DEEPGRAM_API_KEY: '',
    AIDM_TTS_PROVIDER: '',
    AIDM_OPENAI_API_KEY: '',
    OPENAI_API_KEY: '',
    AIDM_AUTH_REQUIRED: 'false',
    AIDM_TELEMETRY_ENABLED: 'false',
    AIDM_SECURITY_HEADERS_ENABLED: 'true',
    AIDM_SERVE_FRONTEND: SINGLE_ORIGIN_BUILD ? 'true' : 'false',
    AIDM_FRONTEND_DIST_DIR: FRONTEND_DIST_DIR,
    AIDM_SOCKETIO_ASYNC_MODE: 'threading',
    AIDM_SOCKETIO_WORKER_MODEL: 'single',
    AIDM_CORS_ALLOWLIST: SINGLE_ORIGIN_BUILD ? backendUrl : frontendUrl,
    AIDM_SOCKET_CORS_ALLOWLIST: SINGLE_ORIGIN_BUILD ? backendUrl : frontendUrl,
  }
  const backendOptions = { cwd: REPO_ROOT, env: backendEnv }

  if (BACKEND_SERVER === 'gunicorn') {
    log(`preparing isolated backend on ${backendUrl}`)
    runToCompletion(
      PYTHON,
      ['-m', 'aidm_server.deploy_bootstrap', '--check-only', '--host', '127.0.0.1', '--port', String(backendPort)],
      backendOptions,
      'Backend preflight',
    )
    log(`starting isolated backend with Gunicorn gthread on ${backendUrl}`)
    spawnManaged(
      PYTHON,
      [
        '-m',
        'gunicorn',
        '--worker-class',
        'gthread',
        '--workers',
        '1',
        '--threads',
        '16',
        '--bind',
        `127.0.0.1:${backendPort}`,
        '--timeout',
        '60',
        '--no-control-socket',
        '--error-logfile',
        '-',
        'aidm_server.wsgi:app',
      ],
      backendOptions,
    )
  } else {
    log(`starting isolated backend with Werkzeug on ${backendUrl}`)
    spawnManaged(
      PYTHON,
      ['-m', 'aidm_server.deploy_bootstrap', '--host', '127.0.0.1', '--port', String(backendPort)],
      backendOptions,
    )
  }
  const healthResponse = await waitForHttp(`${backendUrl}/api/health`, 'backend health')
  assertSecurityHeaders(healthResponse, 'backend health')

  if (SINGLE_ORIGIN_BUILD) {
    await checkSingleOriginServer(backendUrl)
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
  log(PASSED_MESSAGE)
}

if (require.main === module) {
  withTimeout(main(), SMOKE_TOTAL_TIMEOUT_MS, 'Browser smoke')
    .catch(async (error) => {
      await shutdown()
      console.error(`[browser-smoke][error] ${error instanceof Error ? error.message : String(error)}`)
      process.exit(1)
    })
}

module.exports = { launchChromium }
