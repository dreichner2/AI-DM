import { describe, expect, it } from 'vitest'
import type { InspectorPanelProps } from '../InspectorPanel'
import type { Player } from '../types'
import { partyWithViewer, viewerHero } from './viewerHero'

const player = { player_id: 30, character_name: 'Ysolde Marrow', name: 'Danny', race: 'Elf', sex: 'female' } as Player
const live = { id: 30, character_name: 'Ysolde Marrow', name: 'Danny', health: { tone: 'wounded' as const, label: 'Wounded', currentHp: 9, maxHp: 24 } }
const other = { id: 31, character_name: 'Brannoc', name: 'Imogen' }

function sheet(hp: string) {
  return { characterAvatarSrc: '/profile-icons/elf_female.png', statBlock: { hp } as InspectorPanelProps['statBlock'] }
}

describe('viewerHero', () => {
  it('prefers the live roster', () => {
    expect(viewerHero({ activePlayers: [other, live], selectedPlayer: player }, sheet('24 / 24'), 30)).toBe(live)
  })

  it('describes the hero from their sheet until the roster lists them', () => {
    expect(viewerHero({ activePlayers: [], selectedPlayer: player }, sheet('6 / 24'), 30)).toEqual({
      id: 30,
      character_name: 'Ysolde Marrow',
      name: 'Danny',
      race: 'Elf',
      sex: 'female',
      profile_image: '/profile-icons/elf_female.png',
      health: { tone: 'badly-wounded', label: 'Badly wounded', currentHp: 6, maxHp: 24 },
    })
    // Without a maximum the sheet gives no health to show.
    expect(viewerHero({ activePlayers: [], selectedPlayer: player }, sheet('—'), 30)?.health).toBeNull()
    expect(viewerHero({ activePlayers: [], selectedPlayer: null }, sheet('24 / 24'), 30)).toBeNull()
    expect(viewerHero({ activePlayers: [], selectedPlayer: player }, sheet('24 / 24'), null)).toBeNull()
  })

  it('leads the party with the hero, once', () => {
    const fromSheet = viewerHero({ activePlayers: [other], selectedPlayer: player }, sheet('24 / 24'), 30)
    expect(partyWithViewer([other], fromSheet).map((member) => member.id)).toEqual([30, 31])
    expect(partyWithViewer([other, live], live)).toEqual([other, live])
    expect(partyWithViewer([other], null)).toEqual([other])
  })
})
