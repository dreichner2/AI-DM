// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { accountSheetChunk } from './story/storyChunks'
import type { TitleHero } from './story/TitleHeroes'
import { TitleScreen, type TitleResume, type TitleScreenProps } from './TitleScreen'

const HEROES: TitleHero[] = [
  {
    id: 'arden-vale',
    name: 'Arden Vale',
    race: 'Human',
    charClass: 'Fighter',
    tagline: 'A steady sword arm who protects the road when words fail.',
    portrait: '/profile-icons/human_male.png',
  },
  {
    id: 'liora-quill',
    name: 'Liora Quill',
    race: 'Elf',
    charClass: 'Wizard',
    tagline: 'A sharp-eyed scholar who reads omens in ink, ash, and old stone.',
    portrait: '/profile-icons/elf_female.png',
  },
  {
    id: 'mara-fen',
    name: 'Mara Fen',
    race: 'Halfling',
    charClass: 'Rogue',
    tagline: 'A quick hand and quicker grin, useful when roads hide locked answers.',
    portrait: '/profile-icons/halfling_female.png',
  },
]

function resumeFixture(overrides: Partial<TitleResume> = {}): TitleResume {
  return {
    title: 'The Road of Unremembered Kings',
    chapterNumber: 3,
    scene: 'The Lantern Post Inn',
    lastPlayedAt: new Date(Date.now() - 2 * 60 * 60 * 1000 - 60_000).toISOString(),
    continueLine: 'The Lantern Post Inn — At dusk, an empty caravan rolls into the Lantern Post.',
    begun: true,
    ...overrides,
  }
}

function makeProps(overrides: Partial<TitleScreenProps> = {}): TitleScreenProps {
  return {
    pending: false,
    accountReady: true,
    heroes: HEROES,
    heroesStatus: 'ready',
    selectedHeroId: 'arden-vale',
    onSelectHero: vi.fn(),
    resume: null,
    safeMode: false,
    onPlayNow: vi.fn(),
    onContinue: vi.fn(),
    onCreateCampaign: vi.fn(),
    onOpenAdventures: vi.fn(),
    onLogIn: vi.fn(),
    onCreateAccount: vi.fn(),
    ...overrides,
  }
}

function renderTitleScreen(overrides: Partial<TitleScreenProps> = {}) {
  const props = makeProps(overrides)
  return { props, ...render(<TitleScreen {...props} />) }
}

/** Holds the hero choice the way usePlayNowOnboarding does. */
function StatefulTitleScreen(props: TitleScreenProps) {
  const [selectedHeroId, setSelectedHeroId] = useState(props.selectedHeroId)
  return (
    <TitleScreen
      {...props}
      selectedHeroId={selectedHeroId}
      onSelectHero={(heroId) => {
        setSelectedHeroId(heroId)
        props.onSelectHero(heroId)
      }}
    />
  )
}

describe('TitleScreen', () => {
  afterEach(cleanup)

  it('greets a new player with the ready-made adventure and its heroes', () => {
    const { props } = renderTitleScreen()

    expect(screen.getByRole('heading', { level: 1, name: 'AI-DM' })).toBeInTheDocument()
    expect(screen.getByText('A Dungeon Master that remembers.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'The Road of Unremembered Kings' })).toBeInTheDocument()
    expect(screen.getByText(/Every name in its ledger has been scratched out\./)).toBeInTheDocument()
    expect(screen.queryByText('Continue your adventure')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument()

    const group = screen.getByRole('radiogroup', { name: 'Choose your hero' })
    expect(within(group).getAllByRole('radio')).toHaveLength(3)
    expect(within(group).getByRole('radio', { name: 'Arden Vale' })).toBeChecked()
    expect(within(group).getByRole('radio', { name: 'Mara Fen' })).toHaveAccessibleDescription('Halfling Rogue')
    expect(screen.getByText('A steady sword arm who protects the road when words fail.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Create your own adventure' }))
    expect(props.onCreateCampaign).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'Log in' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Play now/i })).toHaveAccessibleName('Play now as Arden Vale')
  })

  it('offers a returning player their latest adventure first', () => {
    const { props } = renderTitleScreen({ resume: resumeFixture() })

    expect(screen.getByRole('heading', { name: 'Continue your adventure' })).toBeInTheDocument()
    expect(screen.getByText('The Road of Unremembered Kings')).toBeInTheDocument()
    expect(screen.getByText('Chapter 3 · played 2 hours ago')).toBeInTheDocument()
    expect(screen.getByText('The Lantern Post Inn — At dusk, an empty caravan rolls into the Lantern Post.')).toBeInTheDocument()
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Play now/i })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.click(screen.getByRole('button', { name: 'New adventure' }))
    fireEvent.click(screen.getByRole('button', { name: 'All adventures' }))
    expect(props.onContinue).toHaveBeenCalledOnce()
    expect(props.onCreateCampaign).toHaveBeenCalledOnce()
    expect(props.onOpenAdventures).toHaveBeenCalledOnce()
  })

  it('without a continue line, shows only the chapter, the place and when it was played', () => {
    const { container } = renderTitleScreen({ resume: resumeFixture({ continueLine: null }) })

    expect(screen.getByText('Chapter 3 · The Lantern Post Inn · played 2 hours ago')).toBeInTheDocument()
    expect(container.querySelector('.story-title-recap')).toBeNull()
  })

  it('leaves unknown parts out of the resume line', () => {
    renderTitleScreen({
      resume: resumeFixture({ chapterNumber: null, scene: null, lastPlayedAt: null, continueLine: null }),
    })

    expect(screen.getByText('The Road of Unremembered Kings')).toBeInTheDocument()
    expect(screen.queryByText(/Chapter|played/)).not.toBeInTheDocument()
  })

  it('reads Not yet begun for an adventure with no turns, where it would say when it was played', () => {
    renderTitleScreen({
      resume: resumeFixture({ chapterNumber: null, scene: null, lastPlayedAt: new Date().toISOString(), continueLine: null, begun: false }),
    })

    expect(screen.getByText('Not yet begun')).toBeInTheDocument()
    expect(screen.queryByText(/played/)).not.toBeInTheDocument()
  })

  it('reveals the ready-made adventure in place for a returning player', () => {
    const { props } = renderTitleScreen({ resume: resumeFixture() })
    const reveal = screen.getByRole('button', { name: 'Play a ready-made adventure' })
    expect(reveal).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(reveal)

    expect(reveal).toHaveAttribute('aria-expanded', 'true')
    const block = document.getElementById(reveal.getAttribute('aria-controls') ?? '')
    expect(block).not.toBeNull()
    expect(within(block!).getByRole('radiogroup', { name: 'Choose your hero' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeInTheDocument()
    // "New adventure" above already starts a campaign of their own.
    expect(screen.queryByRole('button', { name: 'Create your own adventure' })).not.toBeInTheDocument()

    fireEvent.click(within(block!).getByRole('button', { name: 'Play now as Arden Vale' }))
    expect(props.onPlayNow).toHaveBeenCalledExactlyOnceWith({ fresh: true })

    fireEvent.click(reveal)
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
  })

  it('chooses a hero with the arrow keys and keeps one tab stop', () => {
    const onSelectHero = vi.fn()
    render(<StatefulTitleScreen {...makeProps({ onSelectHero })} />)
    const arden = screen.getByRole('radio', { name: 'Arden Vale' })
    const liora = screen.getByRole('radio', { name: 'Liora Quill' })
    const mara = screen.getByRole('radio', { name: 'Mara Fen' })
    expect(arden).toHaveAttribute('tabindex', '0')
    expect(liora).toHaveAttribute('tabindex', '-1')

    arden.focus()
    fireEvent.keyDown(arden, { key: 'ArrowRight' })
    expect(onSelectHero).toHaveBeenLastCalledWith('liora-quill')
    expect(liora).toBeChecked()
    expect(liora).toHaveFocus()
    expect(liora).toHaveAttribute('tabindex', '0')
    expect(arden).toHaveAttribute('tabindex', '-1')
    expect(screen.getByText('A sharp-eyed scholar who reads omens in ink, ash, and old stone.')).toBeInTheDocument()

    fireEvent.keyDown(liora, { key: 'ArrowLeft' })
    fireEvent.keyDown(arden, { key: 'ArrowUp' })
    expect(mara).toBeChecked()
    expect(mara).toHaveFocus()

    fireEvent.keyDown(mara, { key: 'Home' })
    expect(arden).toBeChecked()
    fireEvent.keyDown(arden, { key: 'End' })
    expect(mara).toBeChecked()
    fireEvent.keyDown(mara, { key: 'ArrowDown' })
    expect(arden).toBeChecked()
  })

  it('names the chosen hero on the Play button and plays as them', () => {
    const props = makeProps()
    render(<StatefulTitleScreen {...props} />)

    fireEvent.click(screen.getByRole('radio', { name: 'Mara Fen' }))
    const play = screen.getByRole('button', { name: /^Play now/i })
    expect(play).toHaveAccessibleName('Play now as Mara Fen')
    fireEvent.click(play)

    expect(props.onSelectHero).toHaveBeenCalledWith('mara-fen')
    expect(props.onPlayNow).toHaveBeenCalledExactlyOnceWith({ fresh: false })
  })

  it('shows the adventure being prepared and locks the choices', () => {
    renderTitleScreen({ pending: true })

    const preparing = screen.getByRole('button', { name: 'Preparing the adventure' })
    expect(preparing).toBeDisabled()
    expect(preparing).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('radio', { name: 'Liora Quill' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /^Play now/i })).not.toBeInTheDocument()
  })

  it('offers account actions while signed out and still plays as a guest', () => {
    const { props } = renderTitleScreen({ accountReady: false })

    fireEvent.click(screen.getByRole('button', { name: 'Log in' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))
    fireEvent.click(screen.getByRole('button', { name: 'Play now as Arden Vale' }))

    expect(props.onLogIn).toHaveBeenCalledOnce()
    expect(props.onCreateAccount).toHaveBeenCalledOnce()
    expect(props.onPlayNow).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Create your own adventure' })).toBeInTheDocument()
  })

  it('starts fetching the account sheet when the pointer or focus reaches Log in or Create account', () => {
    const preload = vi.spyOn(accountSheetChunk, 'preload').mockImplementation(() => undefined)
    renderTitleScreen({ accountReady: false })

    fireEvent.pointerEnter(screen.getByRole('button', { name: 'Log in' }))
    expect(preload).toHaveBeenCalledOnce()
    fireEvent.focus(screen.getByRole('button', { name: 'Create account' }))
    expect(preload).toHaveBeenCalledTimes(2)
    preload.mockRestore()
  })

  it('falls back to the default hero without a picker when the heroes cannot be loaded', () => {
    const { props } = renderTitleScreen({ heroes: [], heroesStatus: 'failed', selectedHeroId: null })

    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Play now as Arden Vale' }))
    expect(props.onPlayNow).toHaveBeenCalledOnce()
  })

  it('keeps Play Now available while the heroes load', () => {
    const { props } = renderTitleScreen({ heroes: [], heroesStatus: 'loading', selectedHeroId: null })

    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Play now' }))
    expect(props.onPlayNow).toHaveBeenCalledOnce()
  })

  it('says when the scripted narrator is running', () => {
    const message = 'Scripted narrator — no AI provider is connected. You can add one in Settings.'
    const { rerender } = renderTitleScreen({ safeMode: true })
    expect(screen.getByText(message)).toBeInTheDocument()

    rerender(<TitleScreen {...makeProps({ safeMode: false })} />)
    expect(screen.queryByText(message)).not.toBeInTheDocument()
  })

  it('is a boxless Story surface over the hearth atmosphere', () => {
    const { container } = renderTitleScreen()
    const section = container.querySelector('section.title-screen')

    expect(section).toHaveClass('story-shell')
    expect(section).toHaveAttribute('data-mood', 'hearth')
    expect(section).toHaveAttribute('aria-labelledby', 'title-screen-heading')
    expect(section?.querySelector('.story-atmosphere')).not.toBeNull()
  })
})
