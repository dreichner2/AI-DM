import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  INITIATIVE_ROLL_REASON,
  abilityActionText,
  buildActionIntent,
  composerTextForMode,
  createClientMessageId,
  diceRollMessage,
  hasReservedAdminPrefix,
  itemActionText,
  parseRollModifier,
  resolveRoll,
  spellActionText,
  stripComposerCommand,
  type AbilityOption,
  type ItemOption,
} from './gameActions'

const strength: AbilityOption = {
  key: 'strength',
  label: 'STR',
  score: '16',
  modifier: '+3',
}

const initiative: AbilityOption = {
  key: 'dexterity',
  label: 'Initiative',
  score: '12',
  modifier: '+1',
}

const potion: ItemOption = {
  name: 'Healing Potion',
  quantity: '2',
}

describe('game action helpers', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses Web Crypto for client IDs and fails closed when it is unavailable', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (values: Uint8Array) => {
        values.set(Array.from({ length: values.length }, (_, index) => index))
        return values
      },
    })

    expect(createClientMessageId()).toBe('local-000102030405060708090a0b0c0d0e0f')

    vi.stubGlobal('crypto', undefined)
    expect(() => createClientMessageId()).toThrow('Secure client ID generation requires the Web Crypto API.')
  })

  it('rewrites composer prefixes without stacking old modes', () => {
    expect(composerTextForMode('ooc', 'I roll a d20: test', 'Ember', 'd20')).toBe('[OOC] test')
    expect(composerTextForMode('ooc', 'I roll a d20+3 for STR check: test', 'Ember', 'd20')).toBe(
      '[OOC] test',
    )
    expect(composerTextForMode('admin', '[OOC] force the door open', 'Ember', 'd20')).toBe(
      '[ADMIN] force the door open',
    )
    expect(composerTextForMode('roll', '[OOC] lift the gate', 'Ember', 'd20', strength)).toBe(
      'I roll a d20+3 for STR check: lift the gate',
    )
    expect(composerTextForMode('roll', 'brace for combat', 'Ember', 'd20', initiative)).toBe(
      'I roll for initiative: brace for combat',
    )
    expect(composerTextForMode('ooc', 'I roll for initiative: brace for combat', 'Ember', 'd20')).toBe(
      '[OOC] brace for combat',
    )
    expect(composerTextForMode('ability', '[OOC] lift the gate', 'Ember', 'd20', strength)).toBe(
      'Ember attempts a STR check (+3): lift the gate',
    )
    expect(composerTextForMode('spell', '[OOC] light the sigil', 'Ember', 'd20', strength, null, null, 'speak_to', 'use', '', '', 'Fire Bolt')).toBe(
      'Ember casts Fire Bolt: light the sigil',
    )
    expect(stripComposerCommand('/admin force the door open')).toBe('force the door open')
    expect(stripComposerCommand('(ADMIN) force the door open')).toBe('force the door open')
    expect(stripComposerCommand('/ADMIN/ force the door open')).toBe('force the door open')
    expect(stripComposerCommand('/emote waves')).toBe('waves')
    expect(stripComposerCommand('Ember uses Healing Potion: test the sigil')).toBe('test the sigil')
    expect(stripComposerCommand('Ember casts Fire Bolt: light the sigil')).toBe('light the sigil')
    expect(stripComposerCommand('I cast a spell: light the sigil')).toBe('light the sigil')
  })

  it('detects reserved admin-looking prefixes without matching ordinary admin words', () => {
    expect(hasReservedAdminPrefix('[ADMIN] open the vault')).toBe(true)
    expect(hasReservedAdminPrefix('(ADMIN) open the vault')).toBe(true)
    expect(hasReservedAdminPrefix('/ADMIN/ open the vault')).toBe(true)
    expect(hasReservedAdminPrefix('/ADMIN open the vault')).toBe(true)
    expect(hasReservedAdminPrefix('I ask the admin for help')).toBe(false)
    expect(hasReservedAdminPrefix('/administer the potion')).toBe(false)
  })

  it('builds ability and item text from selected character data', () => {
    expect(abilityActionText('Ember', strength, 'force the latch')).toBe(
      'Ember attempts a STR check (+3): force the latch',
    )
    expect(itemActionText('Ember', 'use', potion.name, 'before opening the door')).toBe(
      'Ember uses Healing Potion: before opening the door',
    )
    expect(itemActionText('Ember', 'buy', 'rope', 'before leaving', '5')).toBe(
      'Ember tries to buy rope for 5 gold: before leaving',
    )
    expect(spellActionText('Ember', 'Fire Bolt', 'light the sigil')).toBe(
      'Ember casts Fire Bolt: light the sigil',
    )
    expect(spellActionText('I', 'Fire Bolt', 'light the sigil')).toBe(
      'I cast Fire Bolt: light the sigil',
    )
  })

  it('calculates advantage rolls with modifier and hidden result metadata', () => {
    const rolls = [7, 18]
    const roll = resolveRoll(
      {
        die: 'd20',
        mode: 'advantage',
        modifier: parseRollModifier('+2'),
        reason: 'ward',
        resultVisibility: 'hidden_until_landed',
      },
      () => rolls.shift() ?? 1,
    )

    expect(roll.rolls).toEqual([7, 18])
    expect(roll.kept).toBe(18)
    expect(roll.total).toBe(20)
    expect(diceRollMessage(roll)).toBe('I roll a d20+2 for ward: 18 (advantage; rolls 7, 18) = 20')
  })

  it('formats initiative rolls with the dexterity total first', () => {
    const roll = resolveRoll(
      {
        die: 'd20',
        mode: 'normal',
        modifier: parseRollModifier(initiative.modifier),
        reason: INITIATIVE_ROLL_REASON,
        resultVisibility: 'hidden_until_landed',
      },
      () => 14,
    )

    expect(roll.total).toBe(15)
    expect(diceRollMessage(roll)).toBe('I roll for initiative: 15 (d20 14 +1 DEX)')
  })

  it('builds typed action metadata for backend persistence', () => {
    const roll = resolveRoll(
      {
        die: 'd20',
        mode: 'normal',
        modifier: 1,
        reason: 'trap',
        resultVisibility: 'hidden_until_landed',
        targetPendingTurnId: 42,
      },
      () => 12,
    )

    const intent = buildActionIntent({
      mode: 'roll',
      message: diceRollMessage(roll),
      clientMessageId: 'local-test',
      source: 'dice_roller',
      roll,
      ability: strength,
    })

    expect(intent.kind).toBe('roll')
    expect(intent.client_message_id).toBe('local-test')
    expect(intent.roll).toMatchObject({
      die: 'd20',
      kept: 12,
      modifier: 1,
      total: 13,
      result_visibility: 'hidden_until_landed',
      target_pending_turn_id: 42,
    })
    expect(intent.ability).toEqual({
      key: 'strength',
      label: 'STR',
      modifier: 3,
    })

    const itemIntent = buildActionIntent({
      mode: 'item',
      message: 'I buy rope for 5 gold.',
      clientMessageId: 'buy-rope',
      inventoryAction: 'buy',
      itemName: 'rope',
      itemQuantity: '1',
      costGold: '5',
    })

    expect(itemIntent).toMatchObject({
      kind: 'item',
      inventory_action: 'buy',
      cost_gold: 5,
      item: { name: 'rope', quantity: 1 },
    })

    const staleItemIntent = buildActionIntent({
      mode: 'item',
      message: 'i say: good morrow Vesra my name is Lin nice to meet you',
      clientMessageId: 'stale-rapier',
      inventoryAction: 'use',
      itemName: 'Rapier',
      itemQuantity: '1',
    })

    expect(staleItemIntent).toMatchObject({
      kind: 'message',
    })
    expect(staleItemIntent.item).toBeUndefined()
    expect(staleItemIntent.inventory_action).toBeUndefined()

    const actualItemIntent = buildActionIntent({
      mode: 'item',
      message: 'Lin uses Rapier: cut the rope',
      clientMessageId: 'use-rapier',
      inventoryAction: 'use',
      itemName: 'Rapier',
      itemQuantity: '1',
    })

    expect(actualItemIntent).toMatchObject({
      kind: 'item',
      inventory_action: 'use',
      item: { name: 'Rapier', quantity: 1 },
    })

    const spellIntent = buildActionIntent({
      mode: 'spell',
      message: 'Ember casts Fire Bolt: light the sigil',
      clientMessageId: 'spell-fire-bolt',
      ability: strength,
      spellName: 'Fire Bolt',
    })

    expect(spellIntent).toMatchObject({
      kind: 'spell',
      spell: { name: 'Fire Bolt', effect: 'light the sigil' },
      ability: { key: 'strength', label: 'STR', modifier: 3 },
    })

    const npcInteractionIntent = buildActionIntent({
      mode: 'interact',
      message: 'Ember says to Captain Velra: hello',
      clientMessageId: 'talk-velra',
      interactionType: 'speak_to',
      interactionTarget: {
        kind: 'npc',
        npc_id: 'captain_velra',
        character_name: 'Captain Velra',
        player_name: 'dock captain',
        active: true,
      },
    })

    expect(npcInteractionIntent).toMatchObject({
      kind: 'interact',
      interaction: {
        type: 'speak_to',
        label: 'Speak to',
      },
      target: {
        kind: 'npc',
        npc_id: 'captain_velra',
        character_name: 'Captain Velra',
        player_name: 'dock captain',
      },
    })
  })
})
