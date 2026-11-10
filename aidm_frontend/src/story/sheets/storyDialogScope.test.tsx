/// <reference types="node" />
// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { testBoard, testInspector } from '../dockTestFixtures'
import { StoryDrawer } from '../StoryDrawer'
import type { StoryDrawerTab } from '../types'

const STORY_DIALOG_CSS = readFileSync(resolve(__dirname, '../../styles/story-sheets.css'), 'utf8')
// The one deliberate exception: the player's Reduce motion choice applies to the whole Story shell.
const REDUCE_MOTION_PREFIX = '.story-shell[data-reduce-motion]'

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
      (selector) => !selector.includes('.story-dialog') && !selector.startsWith(REDUCE_MOTION_PREFIX),
    )
    expect(unscoped).toEqual([])
    expect(STORY_DIALOG_CSS).not.toMatch(/\.story-sheet\b(?!s)/)
  })

  it('leaves the character drawer free of dialog classes', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const tabs: StoryDrawerTab[] = ['character', 'pack', 'magic', 'journal', 'map']
    for (const tab of tabs) {
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
