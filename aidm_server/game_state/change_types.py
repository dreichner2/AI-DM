from __future__ import annotations


PHASE_1_STATE_CHANGE_TYPES = {
    'inventory.add',
    'inventory.remove',
    'inventory.transfer',
    'inventory.equip',
    'inventory.unequip',
    'inventory.mark_used',
    'currency.add',
    'currency.remove',
    'currency.transfer',
    'health.heal',
    'health.damage',
    'health.max.set',
    'race_ability.mark_used',
    'race_ability.refresh',
    'spell.learn',
    'spell.cast',
    'class_feature.use',
    'scene.interactable.action',
    'rest.complete',
    'xp.add',
    'xp.remove',
}

WORLD_STATE_CHANGE_TYPES = {
    'scene.update',
    'scene.move_location',
    'scene.item.add',
    'scene.item.remove',
    'location.discover',
    'location.update',
    'location.connect',
    'quest.add',
    'quest.update',
    'quest.objective.add',
    'quest.objective.update',
    'quest.complete',
    'quest.fail',
    'npc.discover',
    'npc.update',
    'npc.move',
    'npc.relationship.update',
    'clue.discover',
    'clue.update',
    'faction.discover',
    'faction.relationship.update',
    'map.reveal',
    'map.region.update',
    'handout.reveal',
    'lore.unlock',
    'flag.set',
    'flag.unset',
}

COMBAT_STATE_CHANGE_TYPES = {
    'combat.start',
    'combat.update',
    'combat.round.advance',
    'combat.participant.update',
    'combat.move',
    'combat.condition.add',
    'combat.condition.remove',
    'combat.ability.mark_used',
    'combat.intent.set',
    'combat.morale.update',
    'combat.morale.event',
    'combat.battlefield.update',
    'combat.end',
}

PLAYER_SNAPSHOT_CHANGE_TYPES = {
    'spell.learn',
    'spell.cast',
    'class_feature.use',
    'rest.complete',
    'inventory.equip',
    'inventory.unequip',
    'health.max.set',
    'race_ability.mark_used',
    'race_ability.refresh',
}

SNAPSHOT_REFRESH_CHANGE_TYPES = WORLD_STATE_CHANGE_TYPES | COMBAT_STATE_CHANGE_TYPES | PLAYER_SNAPSHOT_CHANGE_TYPES

STATE_CHANGE_TYPES = PHASE_1_STATE_CHANGE_TYPES | WORLD_STATE_CHANGE_TYPES | COMBAT_STATE_CHANGE_TYPES

CURRENCY_TYPES = {'pp', 'gp', 'ep', 'sp', 'cp'}
