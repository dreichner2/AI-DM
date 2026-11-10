import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import '../../App.css'
import { TitleScreen, type TitleResume } from '../../TitleScreen'
import type { TitleHero, TitleHeroesStatus } from '../TitleHeroes'

/**
 * Development-only harness for the title screen (story-title-preview.html).
 * `?state=new|returning|starting|signed-out|heroes-failed`, plus optional
 * `safe=1` (no AI provider) and `heroes=loading`.
 */

const PREVIEW_STATES = ['new', 'returning', 'starting', 'signed-out', 'heroes-failed'] as const
type TitlePreviewState = (typeof PREVIEW_STATES)[number]

const params = new URLSearchParams(window.location.search)
const stateParam = params.get('state')
const previewState: TitlePreviewState = PREVIEW_STATES.find((state) => state === stateParam) ?? 'new'

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
  {
    id: 'tovan-ember',
    name: 'Tovan Ember',
    race: 'Dwarf',
    charClass: 'Cleric',
    tagline: 'A keeper of roadside shrines with a lantern, a shield, and stubborn mercy.',
    portrait: '/profile-icons/dwarf_male.png',
  },
]

const RESUME: TitleResume = {
  title: 'The Road of Unremembered Kings',
  chapterNumber: 3,
  scene: 'The Lantern Post Inn',
  lastPlayedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  recap:
    'Mara Reedwake swore the caravan came in empty. Tovin Bell’s ledger says otherwise, and the last name in it, ' +
    'half scratched away, looks a great deal like yours.',
}

const heroesStatus: TitleHeroesStatus =
  previewState === 'heroes-failed' ? 'failed' : params.get('heroes') === 'loading' ? 'loading' : 'ready'

function log(action: string) {
  return () => console.info(`[title preview] ${action}`)
}

export function TitlePreview() {
  const [selectedHeroId, setSelectedHeroId] = useState('arden-vale')
  const [pending, setPending] = useState(previewState === 'starting')

  return (
    <div style={{ display: 'grid', height: '100dvh' }}>
      <TitleScreen
        pending={pending}
        accountReady={previewState !== 'signed-out'}
        heroes={heroesStatus === 'ready' ? HEROES : []}
        heroesStatus={heroesStatus}
        selectedHeroId={heroesStatus === 'ready' ? selectedHeroId : null}
        onSelectHero={setSelectedHeroId}
        resume={previewState === 'returning' ? RESUME : null}
        safeMode={params.get('safe') === '1'}
        onPlayNow={() => setPending(true)}
        onContinue={log('continue')}
        onCreateCampaign={log('create campaign')}
        onOpenAdventures={log('all adventures')}
        onLogIn={log('log in')}
        onCreateAccount={log('create account')}
      />
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TitlePreview />
  </StrictMode>,
)
