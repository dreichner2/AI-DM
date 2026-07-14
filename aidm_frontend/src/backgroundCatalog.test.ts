import { describe, expect, it } from 'vitest'
import {
  PLAYABLE_BACKGROUNDS,
  backgroundFromValue,
  proficiencyLabel,
} from './backgroundCatalog'

describe('backgroundCatalog', () => {
  it('contains a small catalog where every choice changes mechanics', () => {
    expect(PLAYABLE_BACKGROUNDS.map((background) => background.id)).toEqual([
      'acolyte',
      'criminal',
      'folk_hero',
      'guild_artisan',
      'sage',
      'soldier',
    ])
    expect(
      PLAYABLE_BACKGROUNDS.every(
        (background) =>
          background.skillProficiencies.length === 2 &&
          background.toolProficiencies.length >= 1,
      ),
    ).toBe(true)
  })

  it('resolves ids and names without accepting unknown choices', () => {
    expect(backgroundFromValue('Guild Artisan')?.id).toBe('guild_artisan')
    expect(backgroundFromValue('guild_artisan')?.name).toBe('Guild Artisan')
    expect(backgroundFromValue('all_skills_forever')).toBeNull()
    expect(proficiencyLabel('thieves_tools')).toBe('Thieves Tools')
  })
})
