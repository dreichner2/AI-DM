from __future__ import annotations

import re

from aidm_server.action_intent import strip_reserved_admin_prefix
from aidm_server.models import Campaign, Player, Session, safe_json_loads
from aidm_server.rules import RuleHint
from aidm_server.spellbook import known_spell, normalize_spellbook, spellbook_from_character_sheet


_HARMFUL_PVP_RE = re.compile(
    r'\b(?:attack|attacks|attacked|behead\w*|choke\w*|cut|cuts|decapitat\w*|execute\w*|'
    r'hit|hits|kick\w*|kill\w*|maim\w*|murder\w*|punch\w*|shoot\w*|slash\w*|slice\w*|'
    r'slit|smite\w*|stab\w*|strike\w*)\b|\bhead\s+off\b',
    re.IGNORECASE,
)
_GENERIC_PLAYER_RACE_LABELS = {'human', 'elf', 'dwarf', 'gnome', 'halfling'}
_INTERACTION_TARGET_CUE_RE = re.compile(
    r'\b(?:to|at|toward|towards|with|from)\s+(?:the\s+)?(?P<target>[A-Za-z][A-Za-z0-9\'\-\s]{1,80}?)(?:\s*:|[.!?]|$)',
    re.IGNORECASE,
)


class TurnActionPolicy:
    """Pure action-targeting and DM-prompt policy used by the turn coordinator.

    This keeps player/NPC targeting and action-specific prompt construction out
    of ``TurnEngine``. Database lookups, socket emissions, and persistence stay
    with the coordinator; this class only evaluates already-loaded domain data.
    """

    @staticmethod
    def is_admin_override(action_intent: dict | None) -> bool:
        return isinstance(action_intent, dict) and action_intent.get('kind') == 'admin'

    @staticmethod
    def _admin_model_input(user_input: str) -> str:
        clean = strip_reserved_admin_prefix(user_input)
        return (
            'ADMIN OVERRIDE (authenticated):\n'
            f'{clean}\n\n'
            'This is an out-of-character table administrator directive. Make it happen in the next DM response. '
            'Do not ask for a roll, do not defer the outcome, and do not refuse due to normal story uncertainty. '
            'If the directive changes established state, make the change true and give a concise in-world explanation.'
        )

    @staticmethod
    def _interaction_model_input(user_input: str, action_intent: dict | None, actor_label: str) -> str:
        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'interact':
            return user_input
        interaction = action_intent.get('interaction') if isinstance(action_intent.get('interaction'), dict) else {}
        target = action_intent.get('target') if isinstance(action_intent.get('target'), dict) else {}
        target_character = str(target.get('character_name') or 'another player character').strip()
        target_player = str(target.get('player_name') or '').strip()
        target_kind = str(target.get('kind') or 'player').strip().lower()
        interaction_type = str(interaction.get('type') or 'act_on').strip()
        interaction_labels = {
            'speak_to': 'speak to the target',
            'act_on': 'take an action directed at the target',
            'give_to': 'give something to the target',
            'take_from': 'try to take something from the target',
        }
        clean_input = str(user_input or '').strip()
        target_player_line = f'\nTarget account/profile label (not a character): {target_player}' if target_player else ''
        if target_kind == 'npc':
            return (
                'PLAYER-TO-NPC INTERACTION:\n'
                f'Acting character: {actor_label}\n'
                f'Target NPC: {target_character}'
                f'{target_player_line}\n'
                f'Interaction intent: {interaction_labels.get(interaction_type, "interact with the target")}\n\n'
                'Player message:\n'
                f'{clean_input}\n\n'
                'DM handling: Resolve this as an interaction with a current-scene NPC. Ask for a roll when the '
                'action needs one, and only apply inventory, relationship, health, or scene changes when the outcome is clear.'
            )
        return (
            'PLAYER-TO-PLAYER INTERACTION:\n'
            f'Acting character: {actor_label}\n'
            f'Target character: {target_character}'
            f'{target_player_line}\n'
            f'Interaction intent: {interaction_labels.get(interaction_type, "interact with the target")}\n\n'
            'Player message:\n'
            f'{clean_input}\n\n'
            'DM handling: Treat the target as a player character in this campaign, even if they have not spoken in '
            'the current chat log yet. Keep the acting character and target character distinct. Resolve the speech '
            'or action as directed at the target, ask for a roll when the action needs one, and do not narrate the '
            "target player's voluntary response for them."
        )

    @staticmethod
    def _item_model_input(user_input: str, action_intent: dict | None, actor_label: str) -> str:
        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'item':
            return user_input
        item = action_intent.get('item') if isinstance(action_intent.get('item'), dict) else {}
        item_name = str(item.get('name') or 'item').strip()
        quantity = item.get('quantity') or 1
        inventory_action = str(action_intent.get('inventory_action') or 'use').strip()
        cost_gold = action_intent.get('cost_gold')
        cost_line = f'\nKnown price/value: {cost_gold} gold' if cost_gold else ''
        action_labels = {
            'pick_up': 'pick up',
            'buy': 'buy',
            'use': 'use',
            'drop': 'drop',
            'give': 'give',
            'sell': 'sell',
            'equip': 'equip',
            'unequip': 'unequip',
        }
        return (
            'PLAYER INVENTORY INTENT:\n'
            f'Acting character: {actor_label}\n'
            f'Attempted action: {action_labels.get(inventory_action, inventory_action)}\n'
            f'Item: {item_name} x{quantity}'
            f'{cost_line}\n\n'
            'Player message:\n'
            f'{str(user_input or "").strip()}\n\n'
            'DM handling: First inspect the authoritative state-pipeline packet. If matching pre-DM applied changes '
            'already moved, equipped, or consumed this exact item, narrate that validated result and do not duplicate '
            'or reverse it. Otherwise treat this as an attempted inventory action and narrate whether it succeeds. '
            'If it succeeds, explicitly say the character picks up, buys, '
            'drops, gives, sells, consumes, uses up, equips, or unequips the named item so the state pipeline can update inventory. '
            'If it fails, explicitly say why it fails.'
        )

    @staticmethod
    def _travel_model_input(user_input: str, action_intent: dict | None, actor_label: str) -> str:
        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'travel':
            return user_input
        location = action_intent.get('location') if isinstance(action_intent.get('location'), dict) else {}
        return (
            'VALIDATED TRAVEL ACTION:\n'
            f'Acting character: {actor_label}\n'
            f"Destination ID: {location.get('id') or 'unknown'}\n"
            f"Destination name: {location.get('name') or location.get('id') or 'unknown'}\n\n"
            'Player message:\n'
            f'{str(user_input or "").strip()}\n\n'
            'DM handling: Inspect the authoritative pre-DM applied changes. If scene.move_location is present, the '
            'adjacency, accessibility, and combat constraints already passed; narrate arrival at the resulting current '
            'scene without relocating the party again. If no movement was applied, do not invent a successful trip.'
        )

    @staticmethod
    def _pvp_model_input(user_input: str, actor_label: str, target_player: Player) -> str:
        target_label = target_player.character_name or target_player.name or f'Player {target_player.player_id}'
        return (
            'PLAYER-VS-PLAYER ACTION (ALLOWED):\n'
            f'Acting character: {actor_label}\n'
            f'Target player character: {target_label}\n\n'
            'Player message:\n'
            f'{str(user_input or "").strip()}\n\n'
            'DM handling: Allow PvP as an attempted action. Do not reject the attempt just because it targets '
            'another player character. Do not narrate final injury, death, incapacitation, theft, forced movement, '
            'or loss of agency yet. Ask for the appropriate attack roll, opposed check, saving throw, or contested '
            'rolls from the involved players, then defer the final outcome until the required rolls are recorded.'
        )

    @classmethod
    def model_input_for_action(
        cls,
        user_input: str,
        action_intent: dict | None,
        actor_label: str,
        pvp_target: Player | None = None,
    ) -> str:
        if pvp_target:
            return cls._pvp_model_input(user_input, actor_label, pvp_target)
        if cls.is_admin_override(action_intent):
            return cls._admin_model_input(user_input)
        if isinstance(action_intent, dict) and action_intent.get('kind') == 'item':
            return cls._item_model_input(user_input, action_intent, actor_label)
        if isinstance(action_intent, dict) and action_intent.get('kind') == 'interact':
            return cls._interaction_model_input(user_input, action_intent, actor_label)
        if isinstance(action_intent, dict) and action_intent.get('kind') == 'travel':
            return cls._travel_model_input(user_input, action_intent, actor_label)
        return user_input

    @staticmethod
    def player_is_available_for_campaign(player: Player | None, campaign: Campaign) -> bool:
        return bool(
            player
            and player.workspace_id == campaign.workspace_id
            and player.campaign_id == campaign.campaign_id
        )

    @staticmethod
    def target_label_regex(label: str) -> str:
        words = [re.escape(part) for part in re.findall(r'[a-z0-9]+', str(label or '').lower())]
        if not words:
            return ''
        return r'\b' + r'[\W_]+'.join(words) + r"(?:'s|s)?\b"

    @classmethod
    def player_target_labels(cls, player: Player) -> list[str]:
        labels: list[str] = []
        for value in (player.character_name, player.name):
            text = str(value or '').strip()
            if text and text.lower() not in {label.lower() for label in labels}:
                labels.append(text)
        race = str(player.race or '').strip().lower()
        if race and (race not in _GENERIC_PLAYER_RACE_LABELS or race == 'orc'):
            labels.extend([race, f'the {race}'])
        return labels

    @staticmethod
    def contains_harmful_pvp_action(text: str) -> bool:
        return bool(_HARMFUL_PVP_RE.search(text or ''))

    @classmethod
    def harmful_text_targets_player(cls, text: str, player: Player) -> bool:
        if not cls.contains_harmful_pvp_action(text):
            return False
        normalized = str(text or '').lower()
        harmful_pattern = f'(?:{_HARMFUL_PVP_RE.pattern})'
        for label in cls.player_target_labels(player):
            label_pattern = cls.target_label_regex(label)
            if not label_pattern:
                continue
            harm_then_label = re.compile(
                rf'{harmful_pattern}(?:\W+\w+){{0,8}}\W+{label_pattern}',
                re.IGNORECASE,
            )
            label_then_harm = re.compile(
                rf'{label_pattern}(?:\W+\w+){{0,8}}\W+{harmful_pattern}',
                re.IGNORECASE,
            )
            if harm_then_label.search(normalized) or label_then_harm.search(normalized):
                return True
        return False

    @staticmethod
    def current_scene_npc_target(session_obj: Session, target: dict) -> dict | None:
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        if not isinstance(snapshot, dict):
            return None
        scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
        active_npc_ids = {
            str(value).strip()
            for value in scene.get('activeNpcIds', [])
            if str(value or '').strip()
        } if isinstance(scene.get('activeNpcIds'), list) else set()
        scene_location_id = str(scene.get('locationId') or '').strip()
        target_npc_id = str(target.get('npc_id') or target.get('npcId') or '').strip()
        target_name = str(target.get('character_name') or target.get('name') or '').strip().lower()
        npc_records = []
        for key in ('knownNpcs', 'partyNpcs'):
            value = snapshot.get(key)
            if isinstance(value, list):
                npc_records.extend([record for record in value if isinstance(record, dict)])
        for npc in npc_records:
            npc_id = str(npc.get('id') or npc.get('npcId') or '').strip()
            npc_name = str(npc.get('name') or '').strip()
            if target_npc_id and npc_id != target_npc_id:
                continue
            if not target_npc_id and target_name and npc_name.lower() != target_name:
                continue
            if not npc_id and not npc_name:
                continue
            if active_npc_ids and npc_id not in active_npc_ids:
                continue
            npc_location_id = str(npc.get('locationId') or '').strip()
            if not active_npc_ids and npc_location_id and scene_location_id and npc_location_id != scene_location_id:
                continue
            return {
                'npc_id': npc_id or target_npc_id,
                'character_name': npc_name or target.get('character_name') or 'Scene NPC',
                'player_name': str(npc.get('role') or npc.get('disposition') or 'Current scene NPC').strip(),
            }
        return None

    @classmethod
    def current_scene_npc_target_from_text(cls, session_obj: Session, text: str) -> dict | None:
        snapshot = safe_json_loads(session_obj.state_snapshot, {})
        if not isinstance(snapshot, dict):
            return None
        scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
        active_npc_ids = {
            str(value).strip()
            for value in scene.get('activeNpcIds', [])
            if str(value or '').strip()
        } if isinstance(scene.get('activeNpcIds'), list) else set()
        scene_location_id = str(scene.get('locationId') or '').strip()
        npc_records: list[dict] = []
        for key in ('knownNpcs', 'partyNpcs'):
            value = snapshot.get(key)
            if isinstance(value, list):
                npc_records.extend([record for record in value if isinstance(record, dict)])
        if not npc_records:
            return None

        normalized_text = str(text or '').lower()
        explicit_target_terms = {normalized_text}
        for match in _INTERACTION_TARGET_CUE_RE.finditer(text or ''):
            target = str(match.group('target') or '').strip().lower()
            if target:
                explicit_target_terms.add(target)

        matches: list[tuple[int, dict]] = []
        for npc in npc_records:
            npc_id = str(npc.get('id') or npc.get('npcId') or '').strip()
            npc_name = str(npc.get('name') or '').strip()
            if not npc_id or not npc_name:
                continue
            if active_npc_ids and npc_id not in active_npc_ids:
                continue
            npc_location_id = str(npc.get('locationId') or '').strip()
            if not active_npc_ids and npc_location_id and scene_location_id and npc_location_id != scene_location_id:
                continue
            labels = [npc_name, npc_id.replace('_', ' ')]
            aliases = npc.get('aliases') if isinstance(npc.get('aliases'), list) else []
            labels.extend(str(alias) for alias in aliases if str(alias or '').strip())
            best_score = 0
            for label in labels:
                label_text = str(label or '').strip().lower()
                if not label_text:
                    continue
                label_pattern = cls.target_label_regex(label_text)
                if label_pattern and re.search(label_pattern, normalized_text, re.IGNORECASE):
                    best_score = max(best_score, 100 + len(label_text))
                    continue
                if any(label_text == term or label_text in term or term in label_text for term in explicit_target_terms):
                    best_score = max(best_score, 50 + len(label_text))
            if best_score:
                matches.append((best_score, npc))

        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        npc = matches[0][1]
        return {
            'npc_id': str(npc.get('id') or npc.get('npcId') or '').strip(),
            'character_name': str(npc.get('name') or 'Scene NPC').strip(),
            'player_name': str(npc.get('role') or npc.get('disposition') or 'Current scene NPC').strip(),
        }

    @staticmethod
    def pvp_rules_payload(target_player: Player | None) -> dict | None:
        if not target_player:
            return None
        return {
            'allowed': True,
            'requires_contested_resolution': True,
            'target_player_id': target_player.player_id,
            'target_character_name': target_player.character_name or target_player.name or f'Player {target_player.player_id}',
        }

    @staticmethod
    def apply_pvp_rule_hint(rule_hint: RuleHint, pvp_payload: dict | None) -> RuleHint:
        if not pvp_payload:
            return rule_hint
        rule_hint.requires_roll = True
        if not rule_hint.roll_type or rule_hint.roll_type == 'check':
            rule_hint.roll_type = 'attack'
        rule_hint.dc_hint = rule_hint.dc_hint or 'contested by target player or DM-set defense'
        rule_hint.reason = f"Harmful PvP action targeting {pvp_payload['target_character_name']}; contested resolution required"
        rule_hint.confidence = max(rule_hint.confidence or 0.0, 0.97)
        rule_hint.outcome_deferred = True
        return rule_hint

    @staticmethod
    def apply_spell_rule_hint(
        rule_hint: RuleHint,
        action_intent: dict | None,
        player: Player,
    ) -> RuleHint:
        """Do not turn every legal spell cast into a generic player check.

        Casting a spell is normally automatic after preparation and resource
        validation. Only spells explicitly tagged as spell attacks create a
        player roll gate; saving-throw spells are resolved against the target,
        not by asking the caster for an unrelated spell check.
        """

        if not isinstance(action_intent, dict) or action_intent.get('kind') != 'spell':
            return rule_hint
        spell_payload = action_intent.get('spell') if isinstance(action_intent.get('spell'), dict) else {}
        spell_name = str(spell_payload.get('name') or '').strip()
        spellbook = normalize_spellbook(
            spellbook_from_character_sheet(player.character_sheet),
            class_name=player.class_,
        )
        spell = known_spell(spellbook, spell_name) or {}
        tags = {
            str(tag or '').strip().lower().replace('-', '_').replace(' ', '_')
            for tag in (spell.get('tags') or [])
        }
        resolution = str(
            spell.get('resolutionType')
            or spell.get('resolution_type')
            or spell.get('resolution')
            or ''
        ).strip().lower().replace('-', '_').replace(' ', '_')
        requires_attack = bool(
            spell.get('requiresAttackRoll') is True
            or spell.get('requires_attack_roll') is True
            or resolution in {'attack', 'spell_attack', 'ranged_spell_attack', 'melee_spell_attack'}
            or tags.intersection({'attack', 'spell_attack', 'ranged_spell_attack', 'melee_spell_attack'})
        )
        target_ids = spell_payload.get('target_ids') or spell_payload.get('targetIds') or []
        if spell.get('authoritativeEffect') is True and isinstance(target_ids, list) and target_ids:
            rule_hint.requires_roll = False
            rule_hint.roll_type = None
            rule_hint.dc_hint = None
            rule_hint.reason = (
                f'{spell_name or "Spell"} uses the authoritative targeted-spell resolver; '
                'its attack or saving throw is resolved before narration.'
            )
            rule_hint.confidence = max(rule_hint.confidence or 0.0, 1.0)
            rule_hint.roll_value = None
            rule_hint.outcome_deferred = False
            return rule_hint
        if requires_attack:
            rule_hint.requires_roll = True
            rule_hint.roll_type = 'spell_attack'
            rule_hint.reason = f'{spell_name or "Spell"} requires an authoritative spell attack roll.'
            rule_hint.outcome_deferred = True
            return rule_hint

        rule_hint.requires_roll = False
        rule_hint.roll_type = None
        rule_hint.dc_hint = None
        rule_hint.reason = (
            f'{spell_name or "Spell"} casting is automatic after authoritative preparation and resource validation; '
            'resolve any target saving throw separately.'
        )
        rule_hint.confidence = max(rule_hint.confidence or 0.0, 0.99)
        rule_hint.roll_value = None
        rule_hint.outcome_deferred = False
        return rule_hint
