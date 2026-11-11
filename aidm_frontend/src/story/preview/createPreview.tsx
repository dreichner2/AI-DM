import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import '../../App.css'
import { CreateHero } from '../create/CreateHero'
import {
  CREATE_STEPS,
  ancestryById,
  emptyDraft,
  toggleExpertise,
  toggleSkill,
  withAncestry,
  withBackground,
  withCalling,
  type CreateHeroPayload,
  type CreateStep,
  type HeroDraft,
} from '../create/createHeroModel'
import type { SkillId } from '../create/creationRules'

/**
 * Development-only harness for hero creation (story-create-preview.html).
 * `?step=who|calling|shape|review&class=wizard|fighter|rogue`, plus optional
 * `fresh=1` (nothing chosen yet) and `error=1` (the server refuses the hero).
 */

type Preset = {
  name: string
  ancestry: string
  sex: HeroDraft['sex']
  calling: string
  background: string
  skills: SkillId[]
  expertise?: SkillId[]
}

const PRESETS: Record<string, Preset> = {
  wizard: {
    name: 'Liora Quill',
    ancestry: 'elf',
    sex: 'female',
    calling: 'Wizard',
    background: 'sage',
    skills: ['investigation', 'insight'],
  },
  fighter: {
    name: 'Arden Vale',
    ancestry: 'human',
    sex: 'male',
    calling: 'Fighter',
    background: 'soldier',
    skills: ['perception', 'survival'],
  },
  rogue: {
    name: 'Mara Fen',
    ancestry: 'halfling',
    sex: 'female',
    calling: 'Rogue',
    background: 'criminal',
    skills: ['acrobatics', 'perception', 'sleight_of_hand', 'investigation'],
    expertise: ['stealth', 'sleight_of_hand'],
  },
}

const params = new URLSearchParams(window.location.search)
const step: CreateStep = CREATE_STEPS.find((entry) => entry === params.get('step')) ?? 'who'
const preset = PRESETS[params.get('class') ?? ''] ?? PRESETS.wizard

function presetDraft(): HeroDraft {
  if (params.get('fresh') === '1') return emptyDraft()
  const ancestry = ancestryById(preset.ancestry)
  let draft: HeroDraft = { ...emptyDraft(), name: preset.name, sex: preset.sex }
  if (ancestry) draft = withAncestry(draft, ancestry)
  if (step === 'who') return draft
  draft = withCalling(draft, preset.calling)
  if (step === 'calling') return draft
  draft = withBackground(draft, preset.background)
  draft = preset.skills.reduce(toggleSkill, draft)
  return (preset.expertise ?? []).reduce(toggleExpertise, draft)
}

function create(payload: CreateHeroPayload) {
  console.info('[create preview] payload', payload)
  return new Promise<void>((resolve, reject) => {
    window.setTimeout(() => {
      if (params.get('error') === '1') reject(new Error('The table is full: this campaign already has six heroes.'))
      else resolve()
    }, 900)
  })
}

export function CreatePreview() {
  const [round, setRound] = useState(0)
  return (
    <CreateHero
      key={round}
      auth=""
      baseUrl=""
      campaignTitle="The Road of Unremembered Kings"
      initialDraft={presetDraft()}
      initialStep={step}
      onCreate={create}
      onClose={() => setRound((current) => current + 1)}
    />
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CreatePreview />
  </StrictMode>,
)
