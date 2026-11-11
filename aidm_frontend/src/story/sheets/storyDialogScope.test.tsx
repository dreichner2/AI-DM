/// <reference types="node" />
// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRef } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BackgroundSelector } from '../../BackgroundSelector'
import { ModalShell } from '../../ModalShell'
import { testBoard, testInspector } from '../dockTestFixtures'
import { StoryDrawer } from '../StoryDrawer'
import type { StoryDrawerTab } from '../types'
import { StoryAccountSheet, type AccountAccessProps } from './StoryAccountSheet'
import { StorySettingsSheet } from './StorySettingsSheet'

const STORY_DIALOG_CSS = readFileSync(resolve(__dirname, '../../styles/story-sheets.css'), 'utf8')
const STORY_DRAWER_CSS = readFileSync(resolve(__dirname, '../../styles/story-drawer.css'), 'utf8')
const DRAWER_TABS: StoryDrawerTab[] = ['character', 'pack', 'magic', 'journal', 'map']
const DIALOG_CLASS = '[class*="story-dialog"]'
const DRAWER_CLASS = '[class*="story-sheet"], [class*="story-drawer"]'
// The player's Reduce motion choice applies to the whole Story shell.
const REDUCE_MOTION_PREFIX = '.story-shell[data-reduce-motion]'
// Shared on purpose with Table tools: the scene music player uses the Settings slider, and the
// Party tab's character block uses the drawer's character rules.
const SHARED_SLIDER_PREFIX = '.scene-music-player '
const SHARED_CHARACTER_PREFIX = '.character-panel '

/** Every selector in a stylesheet, with @-rule preludes and keyframe steps left out. */
function selectors(css: string) {
  const preludes = css.replace(/\/\*[\s\S]*?\*\//g, '').match(/[^{}]+(?=\{)/g) ?? []
  return preludes
    .map((prelude) => prelude.trim())
    .filter((prelude) => !prelude.startsWith('@') && !/^(from|to|\d+%)$/.test(prelude))
    .flatMap((prelude) => {
      const parts: string[] = []
      let depth = 0
      let current = ''
      for (const character of prelude) {
        if (character === '(') depth += 1
        if (character === ')') depth -= 1
        if (character === ',' && depth === 0) {
          parts.push(current.trim())
          current = ''
        } else {
          current += character
        }
      }
      return [...parts, current.trim()]
    })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('dialog styles stay inside dialogs', () => {
  it('scopes every rule in story-sheets.css to the dialog root', () => {
    const all = selectors(STORY_DIALOG_CSS)
    expect(all.length).toBeGreaterThan(100)
    const unscoped = all.filter(
      (selector) =>
        !selector.includes('.story-dialog') &&
        !selector.startsWith(REDUCE_MOTION_PREFIX) &&
        !selector.startsWith(SHARED_SLIDER_PREFIX),
    )
    expect(unscoped).toEqual([])
    expect(STORY_DIALOG_CSS).not.toMatch(/\.story-sheet\b(?!s)/)
  })

  it('scopes every rule in story-drawer.css to the drawer root', () => {
    const all = selectors(STORY_DRAWER_CSS)
    expect(all.length).toBeGreaterThan(100)
    const unscoped = all.filter(
      (selector) => !/\.story-(sheet|drawer)\b/.test(selector) && !selector.startsWith(SHARED_CHARACTER_PREFIX),
    )
    expect(unscoped).toEqual([])
    expect(STORY_DRAWER_CSS).not.toMatch(/\.story-dialog/)
  })

  it('renders the drawer and a dialog side by side, neither carrying the other’s classes', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const { container: drawer } = render(
      <StoryDrawer board={testBoard()} inspector={testInspector()} open tab="journal" onTabChange={vi.fn()} onClose={vi.fn()} />,
    )
    render(
      <StorySettingsSheet
        dialogRef={createRef<HTMLElement>()}
        onClose={vi.fn()}
        narrator={null}
        onNarratorChange={vi.fn()}
        narration={{ enabled: false, problem: '' }}
        onNarrationChange={vi.fn()}
        view="story"
        onViewChange={vi.fn()}
        fullscreen={false}
        onFullscreenChange={vi.fn()}
        diagnostics={{ player: 'You are Ember.', table: 'At the Ember Table.', connection: 'Connected.' }}
        onRefresh={vi.fn()}
        onReconnect={vi.fn()}
        onConnectionSettings={vi.fn()}
      />,
    )
    const settings = screen.getByRole('dialog', { name: 'Settings' })

    expect(drawer.querySelector('.story-sheet')).not.toBeNull()
    expect(drawer.querySelector(DIALOG_CLASS)).toBeNull()
    expect(settings.closest('.story-dialog')).not.toBeNull()
    expect(settings.closest(DRAWER_CLASS)).toBeNull()
    expect(settings.querySelector(DRAWER_CLASS)).toBeNull()
  })

  it('keeps every dialog free of drawer classes', () => {
    render(
      <StoryAccountSheet
        view="summary"
        account={{ name: 'Ember Vale', table: 'Ember Table', role: 'owner' }}
        access={{} as AccountAccessProps}
        dialogRef={createRef<HTMLElement>()}
        onClose={vi.fn()}
        onLogOut={vi.fn()}
        onSwitchTable={vi.fn()}
      />,
    )
    render(
      <ModalShell dialogRef={createRef<HTMLElement>()} labelledBy="editor-title" onClose={vi.fn()} className="player-edit-dialog">
        <h2 id="editor-title">Edit Character</h2>
        <BackgroundSelector selectedBackgroundId="sage" pending={false} onBackgroundChange={vi.fn()} />
      </ModalShell>,
    )
    for (const dialog of screen.getAllByRole('dialog')) {
      expect(dialog.closest('.story-dialog')).not.toBeNull()
      expect(dialog.querySelector(DRAWER_CLASS)).toBeNull()
    }
  })

  it('leaves the character drawer free of dialog classes', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    for (const tab of DRAWER_TABS) {
      const { container, unmount } = render(
        <StoryDrawer
          board={testBoard()}
          inspector={testInspector()}
          open
          tab={tab}
          onTabChange={vi.fn()}
          onClose={vi.fn()}
        />,
      )
      expect(container.querySelector('[class*="story-dialog"]')).toBeNull()
      unmount()
    }
  })
})
