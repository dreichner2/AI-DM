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
const SMOKE_TIMEOUT_MS = Number(process.env.AIDM_VISUAL_SMOKE_TIMEOUT_MS || 90_000)
const SHUTDOWN_GRACE_MS = Number(process.env.AIDM_VISUAL_SMOKE_SHUTDOWN_GRACE_MS || 2_000)
const ARTIFACT_ROOT = path.join(REPO_ROOT, 'tmp', 'verification_artifacts', 'visual-smoke')

const children = new Set()
let smokeTempDir = null

const VIEWPORTS = [
  { name: 'desktop-shell', width: 1440, height: 900, fullPage: false },
  { name: 'laptop-shell', width: 1280, height: 800, fullPage: false },
  { name: 'tablet-landscape', width: 1024, height: 768, fullPage: false },
  { name: 'tablet-portrait', width: 768, height: 1024, fullPage: false },
  { name: 'mobile-full', width: 390, height: 844, fullPage: false },
  { name: 'mobile-narrow', width: 360, height: 800, fullPage: false },
  { name: 'mobile-small', width: 320, height: 667, fullPage: false },
  { name: 'mobile-landscape', width: 844, height: 390, fullPage: false },
]

const LIGHT_THEME_VIEWPORTS = [
  { name: 'desktop-light', width: 1440, height: 900, fullPage: false },
  { name: 'mobile-light', width: 390, height: 844, fullPage: false },
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
    stats: {
      ability_scores: {
        strength: 8,
        dexterity: 14,
        constitution: 13,
        intelligence: 15,
        wisdom: 12,
        charisma: 10,
      },
    },
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

  const runtimeNoticeDetails = page.locator('.runtime-notice-details').first()
  if (await runtimeNoticeDetails.count()) {
    const summary = runtimeNoticeDetails.locator('summary')
    await expect(summary).toBeVisible()
    await summary.click()
    await expect(runtimeNoticeDetails.locator('.runtime-notice-full')).toBeVisible()
    await summary.click()
  }

  if (viewport.width > 1100 && viewport.height > 700) {
    const musicPlayer = page.locator('.scene-music-player')
    await expect(musicPlayer).toHaveClass(/is-default-docked/)
    const floatControls = page.getByRole('button', { name: 'Float full music controls' })
    await expect(floatControls).toBeVisible()
    await floatControls.click()
    await expect(musicPlayer).not.toHaveClass(/is-inline-static/)
    await expect(page.getByRole('button', { name: 'Move music player' })).toBeVisible()
    const dockControls = page.getByRole('button', { name: 'Dock music player' })
    await expect(dockControls).toBeVisible()
    await dockControls.click()
    await expect(musicPlayer).toHaveClass(/is-default-docked/)
  }

  if (viewport.width <= 1100) {
    await page.waitForFunction(() => window.matchMedia('(max-width: 1100px)').matches)
    const inspectorToggle = page.locator('.mobile-inspector-toggle')
    await expect(inspectorToggle).toBeVisible()
    if ((await inspectorToggle.getAttribute('aria-expanded')) !== 'true') {
      await inspectorToggle.click()
    }
    await expect(page.locator('.right-inspector')).toBeVisible()
    await page.waitForFunction(() => {
      const inspector = document.querySelector('.right-inspector')
      if (!inspector) return false
      const rect = inspector.getBoundingClientRect()
      return rect.left >= -1 && rect.right <= window.innerWidth + 1
    })

    const tabMetrics = await page.locator('.right-inspector .inspector-tabs').evaluate((tabList) => ({
      clientWidth: tabList.clientWidth,
      left: tabList.getBoundingClientRect().left,
      right: tabList.getBoundingClientRect().right,
      scrollWidth: tabList.scrollWidth,
      tabs: [...tabList.querySelectorAll('[role="tab"]')].map((tab) => ({
        clientWidth: tab.clientWidth,
        label: tab.textContent?.trim() || 'unknown',
        scrollWidth: tab.scrollWidth,
      })),
    }))
    const clippedTab = tabMetrics.tabs.find((tab) => tab.scrollWidth > tab.clientWidth + 1)
    if (clippedTab) {
      throw new Error(`${viewport.name} clips the ${clippedTab.label} inspector tab label`)
    }
    if (tabMetrics.left < -1 || tabMetrics.right > viewport.width + 1) {
      throw new Error(`${viewport.name} inspector tabs extend outside the viewport`)
    }
    if (tabMetrics.tabs.length > 4 && tabMetrics.scrollWidth <= tabMetrics.clientWidth) {
      throw new Error(`${viewport.name} inspector tabs are not horizontally navigable`)
    }
    await page.locator('.right-inspector .inspector-tabs [role="tab"]').last().scrollIntoViewIfNeeded()
    await page.locator('.right-inspector').getByRole('button', { name: 'Close character panel' }).click()
    await expect(page.locator('.right-inspector')).toBeHidden()
  } else {
    await expect(page.locator('.right-inspector')).toBeVisible()
  }

  if (viewport.width > 1100) {
    await expect(page.locator('.scene-music-player')).toHaveClass(/is-inline-static/)
    if (viewport.height <= 700) {
      await expect(page.locator('.scene-music-player')).toHaveClass(/is-short-static/)
    }
  }

  const metrics = await page.evaluate(() => {
    const selectorList = [
      '.prototype-shell',
      '.ops-bar',
      '.session-header',
      '.session-header > div:first-child',
      '.session-actions',
      '.scene-music-player',
      '.roll-wait-banner',
      '.turn-feed',
      '.dm-response-card',
      '.dm-response-card .response-copy',
      '.dm-response-card .stream-state',
      '.dm-response-card .dm-response-actions',
      '.turn-card',
      '.turn-card > p, .turn-card > .narrative-prose, .turn-card > .turn-consequence',
      '.combat-hud',
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
      musicPosition: document.querySelector('.scene-music-player')
        ? window.getComputedStyle(document.querySelector('.scene-music-player')).position
        : null,
      rollWaitPosition: document.querySelector('.roll-wait-banner')
        ? window.getComputedStyle(document.querySelector('.roll-wait-banner')).position
        : null,
    }
  })

  const overflowX = metrics.scrollWidth - metrics.clientWidth
  if (overflowX > 6) {
    throw new Error(`${viewport.name} has horizontal overflow of ${overflowX}px`)
  }

  const visibleTurnFeed = metrics.boxes['.turn-feed']
  const minimumTimelineHeight = viewport.height <= 500 ? 50 : 120
  if (viewport.width <= 1100 && (!visibleTurnFeed || visibleTurnFeed.height < minimumTimelineHeight)) {
    throw new Error(`${viewport.name} leaves only ${visibleTurnFeed?.height ?? 0}px for the session timeline`)
  }

  const compactPhone = viewport.width <= 560 || (viewport.width <= 1100 && viewport.height <= 500)
  if (compactPhone) {
    const composer = metrics.boxes['.action-composer']
    const responseCard = metrics.boxes['.dm-response-card']
    const responseCopy = metrics.boxes['.dm-response-card .response-copy']
    const minimumFeedHeight = viewport.height <= 500 ? 50 : 220
    if (!visibleTurnFeed || visibleTurnFeed.height < minimumFeedHeight) {
      throw new Error(`${viewport.name} leaves only ${visibleTurnFeed?.height ?? 0}px for readable story content`)
    }
    if (!composer || composer.height > 220 || composer.bottom > metrics.viewport.height + 1) {
      throw new Error(`${viewport.name} composer consumes ${composer?.height ?? 0}px or is clipped`)
    }
    if (visibleTurnFeed && visibleTurnFeed.bottom > composer.top + 1) {
      throw new Error(`${viewport.name} timeline overlaps the action composer`)
    }
    if (
      responseCard &&
      responseCopy &&
      responseCopy.width < responseCard.width * 0.82
    ) {
      throw new Error(
        `${viewport.name} squeezes response copy to ${responseCopy.width}px inside a ${responseCard.width}px card`,
      )
    }
    if (metrics.rollWaitPosition === 'sticky') {
      throw new Error(`${viewport.name} pins the pending-roll banner over the story`)
    }

    const actionMode = page.getByRole('button', { name: 'Return to Action mode' })
    await expect(actionMode).toBeVisible()
    const spellMode = page.locator('.composer-tools').getByRole('button', { name: /Spell/ })
    await spellMode.click()
    await expect(spellMode).toHaveAttribute('aria-pressed', 'true')
    await actionMode.click()
    await expect(actionMode).toHaveAttribute('aria-pressed', 'true')
  }

  if (viewport.width <= 1100) {
    const turnCard = metrics.boxes['.turn-card']
    const turnCopy = metrics.boxes['.turn-card > p, .turn-card > .narrative-prose, .turn-card > .turn-consequence']
    if (turnCard && turnCopy && turnCopy.width < turnCard.width * 0.82) {
      throw new Error(
        `${viewport.name} squeezes turn copy to ${turnCopy.width}px inside a ${turnCard.width}px card`,
      )
    }
    const streamState = metrics.boxes['.dm-response-card .stream-state']
    const responseActions = metrics.boxes['.dm-response-card .dm-response-actions']
    if (
      streamState &&
      responseActions &&
      streamState.right > responseActions.left &&
      streamState.left < responseActions.right &&
      streamState.bottom > responseActions.top &&
      streamState.top < responseActions.bottom
    ) {
      throw new Error(`${viewport.name} response status overlaps its report control`)
    }
  }

  if (viewport.width > 1100) {
    const topBar = metrics.boxes['.ops-bar']
    const composerTools = metrics.boxes['.composer-tools']
    const actionComposer = metrics.boxes['.action-composer']
    const inspector = metrics.boxes['.right-inspector']
    const sessionHeader = metrics.boxes['.session-header']
    const sessionTitle = metrics.boxes['.session-header > div:first-child']
    const sessionActions = metrics.boxes['.session-actions']
    if (!topBar || !composerTools || !actionComposer || !inspector || !sessionHeader || !sessionTitle || !sessionActions) {
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
    if (sessionActions.right > sessionHeader.right + 1 || sessionActions.right > inspector.left + 1) {
      throw new Error(`${viewport.name} session actions collide with the inspector`)
    }
    if (sessionActions.left < sessionTitle.right - 1) {
      throw new Error(`${viewport.name} session actions overlap the session title`)
    }
    const musicPlayer = metrics.boxes['.scene-music-player']
    const rollWaitBanner = metrics.boxes['.roll-wait-banner']
    const turnFeed = metrics.boxes['.turn-feed']
    if (!musicPlayer || !turnFeed || metrics.musicPosition !== 'static') {
      throw new Error(`${viewport.name} default music transport is not docked in the session layout`)
    }
    if (musicPlayer.bottom > turnFeed.top + 1) {
      throw new Error(`${viewport.name} default music transport overlaps the turn feed`)
    }
    if (
      rollWaitBanner &&
      musicPlayer.right > rollWaitBanner.left &&
      musicPlayer.left < rollWaitBanner.right &&
      musicPlayer.bottom > rollWaitBanner.top &&
      musicPlayer.top < rollWaitBanner.bottom
    ) {
      throw new Error(`${viewport.name} default music transport overlaps the pending-roll banner`)
    }
  }
}

async function assertCompactCombatLayout(page, viewport) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await expect(page.locator('.combat-hud')).toBeVisible()
  const phoneLayout = viewport.width <= 560

  const metrics = await page.evaluate(() => {
    const box = (selector) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { bottom: rect.bottom, height: rect.height, top: rect.top }
    }
    const combat = document.querySelector('.combat-hud')
    const firstAction = combat?.querySelector('.combat-hud-option')
    const actions = combat?.querySelector('.combat-hud-actions')
    const overview = combat?.querySelector('.combat-hud-overview')
    const combatRect = combat?.getBoundingClientRect()
    const actionRect = firstAction?.getBoundingClientRect()
    const reasonRect = firstAction?.querySelector('.combat-hud-option-reason')?.getBoundingClientRect()
    const actionsRect = actions?.getBoundingClientRect()
    const overviewRect = overview?.getBoundingClientRect()
    const visibleFirstActionHeight = combatRect && actionRect
      ? Math.max(0, Math.min(combatRect.bottom, actionRect.bottom) - Math.max(combatRect.top, actionRect.top))
      : 0
    return {
      combat: box('.combat-hud'),
      combatClientHeight: combat?.clientHeight ?? 0,
      combatScrollHeight: combat?.scrollHeight ?? 0,
      combatOverflowY: combat ? window.getComputedStyle(combat).overflowY : null,
      composer: box('.action-composer'),
      situationDisplay: window.getComputedStyle(document.querySelector('.session-at-a-glance')).display,
      turnFeed: box('.turn-feed'),
      viewportHeight: window.innerHeight,
      visibleFirstActionHeight,
      firstActionHeight: actionRect?.height ?? 0,
      firstActionWidth: actionRect?.width ?? 0,
      visibleFirstReasonHeight: reasonRect
        ? Math.max(0, Math.min(reasonRect.bottom, actionRect?.bottom ?? 0) - Math.max(reasonRect.top, actionRect?.top ?? 0))
        : null,
      actionsClientWidth: actions?.clientWidth ?? 0,
      actionsScrollWidth: actions?.scrollWidth ?? 0,
      actionsBottom: actionsRect?.bottom ?? 0,
      overviewTop: overviewRect?.top ?? 0,
      overviewDisplay: overview ? window.getComputedStyle(overview).display : 'none',
    }
  })

  if (!metrics.combat || metrics.combat.height > (phoneLayout ? 124 : 142)) {
    throw new Error(`${viewport.name} compact combat panel is ${metrics.combat?.height ?? 0}px tall`)
  }
  if (phoneLayout && metrics.combatOverflowY !== 'hidden') {
    throw new Error(`${viewport.name} compact combat still exposes nested vertical scrolling`)
  }
  if (!phoneLayout && metrics.combatScrollHeight <= metrics.combatClientHeight + 20) {
    throw new Error(`${viewport.name} compact combat fixture is not using one intentional scroll region`)
  }
  if (!metrics.turnFeed || metrics.turnFeed.height < (phoneLayout ? 180 : 120)) {
    throw new Error(`${viewport.name} combat leaves only ${metrics.turnFeed?.height ?? 0}px for the timeline`)
  }
  if (
    !metrics.composer ||
    (phoneLayout && metrics.composer.height > 220) ||
    metrics.composer.bottom > metrics.viewportHeight + 1
  ) {
    throw new Error(`${viewport.name} combat pushes the composer below the viewport`)
  }
  if (phoneLayout && metrics.visibleFirstReasonHeight !== null && metrics.visibleFirstReasonHeight < 10) {
    throw new Error(`${viewport.name} clips the unavailable-action reason`)
  }
  if (metrics.situationDisplay !== 'none') {
    throw new Error(`${viewport.name} compact combat did not replace the redundant situation card`)
  }
  if (
    metrics.visibleFirstActionHeight < (phoneLayout ? 60 : 40) ||
    (phoneLayout && metrics.visibleFirstActionHeight < metrics.firstActionHeight - 1)
  ) {
    throw new Error(
      `${viewport.name} exposes only ${metrics.visibleFirstActionHeight}px of the first combat choice`,
    )
  }
  if (metrics.overviewDisplay !== 'none' && metrics.overviewTop < metrics.actionsBottom) {
    throw new Error(`${viewport.name} combat roster overlaps the action choices`)
  }
  if (phoneLayout && metrics.actionsScrollWidth <= metrics.actionsClientWidth + 20) {
    throw new Error(`${viewport.name} combat choices are not horizontally browsable`)
  }
  if (phoneLayout && metrics.firstActionWidth < 0.72 * viewport.width) {
    throw new Error(`${viewport.name} first combat choice is only ${metrics.firstActionWidth}px wide`)
  }
  if (metrics.turnFeed.bottom > metrics.combat.top + 1) {
    throw new Error(`${viewport.name} timeline overlaps the combat panel`)
  }
  if (metrics.combat.bottom > metrics.composer.top + 1) {
    throw new Error(`${viewport.name} combat panel overlaps the composer`)
  }
}

async function runVisualFlow(frontendUrl, backendUrl, ids, artifactDir) {
  const browser = await chromium.launch(CHROMIUM_CHANNEL ? { channel: CHROMIUM_CHANNEL } : {})
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const consoleErrors = []

  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    const sourceUrl = message.location().url || ''
    const isExpectedCampaignCommentaryMiss =
      text.includes('404') &&
      sourceUrl.includes('/api/sessions/') &&
      sourceUrl.includes('/campaign-pack/commentary')
    if (!isExpectedCampaignCommentaryMiss) consoleErrors.push(text)
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

  await page.evaluate(() => localStorage.setItem('aidm:theme', 'light'))
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.locator('.prototype-shell')).toHaveClass(/theme-light/)
  for (const viewport of LIGHT_THEME_VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await assertLayoutHealth(page, viewport)
    const fileName = `${viewport.name}.png`
    const filePath = path.join(artifactDir, fileName)
    await page.screenshot({ path: filePath, fullPage: viewport.fullPage })
    screenshots.push(filePath)
  }
  await page.evaluate(() => localStorage.setItem('aidm:theme', 'dark'))
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.locator('.prototype-shell')).toHaveClass(/theme-dark/)

  const combatSession = await postJson(backendUrl, '/api/sessions/start', {
    campaign_id: ids.campaign.campaign_id,
  })
  await patchJson(backendUrl, `/api/sessions/${combatSession.session_id}`, {
    name: 'Visual Smoke Combat',
  })
  await postJson(backendUrl, `/api/sessions/${combatSession.session_id}/combat/start`, {
    creature: {
      id: 'visual_smoke_sentry',
      name: 'Visual Smoke Sentry',
      source: 'user_custom',
      creatureType: 'humanoid',
      stats: { maxHp: 14, armorClass: 12 },
    },
    enemyCount: 4,
    encounterGoal: { description: 'Break through the sentry line.' },
  })
  const combatRouteUrl = `${frontendUrl}/?campaign=${ids.campaign.campaign_id}&session=${combatSession.session_id}&player=${ids.player.player_id}`
  await page.goto(combatRouteUrl, { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: /Visual Smoke Combat/i })).toBeVisible()
  await expect(page.locator('.combat-hud')).toBeVisible({ timeout: 15_000 })
  await assertCompactCombatLayout(page, { name: 'tablet-landscape-combat', width: 1024, height: 768 })
  await page.screenshot({ path: path.join(artifactDir, 'combat-tablet-landscape.png') })
  await assertCompactCombatLayout(page, { name: 'mobile-narrow-combat', width: 360, height: 800 })
  await page.screenshot({ path: path.join(artifactDir, 'combat-mobile-narrow.png') })
  log('captured real combat layouts at 1024x768 and 360x800')

  await browser.close()
  if (consoleErrors.length) {
    throw new Error(`Browser console errors: ${consoleErrors.join(' | ')}`)
  }
  return screenshots
}

async function main() {
  if (isPathLikeExecutable(PYTHON) && !fs.existsSync(PYTHON)) {
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
        AIDM_DEBUG: 'false',
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

  await stopAllManaged()
  cleanupTempDir()
  log(`passed: ${screenshots.length} screenshots written under ${path.relative(REPO_ROOT, artifactDir)}`)
}

main()
  .catch(async (error) => {
    await shutdown()
    console.error(`[visual-smoke][error] ${error instanceof Error ? error.message : String(error)}`)
    process.exit(1)
  })
