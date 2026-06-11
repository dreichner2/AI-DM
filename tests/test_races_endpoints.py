from __future__ import annotations

from aidm_server.blueprints.races import _build_custom_race_prompt, _race_payload_from_helper
from aidm_server.database import db
from aidm_server.models import Account, CustomRace, safe_json_dumps


def test_custom_race_prompt_requires_balance_metadata():
    prompt = _build_custom_race_prompt('Create a battle-born custom race.', 'standard')

    assert 'balanceCost' in prompt
    assert 'mechanics.activeAbility' in prompt
    assert 'Do not mark mechanical combat traits as narrative.' in prompt
    assert 'Concept fidelity and source/canon faithfulness come before balance.' in prompt
    assert 'Do not silently nerf' in prompt
    assert 'Only produce a toned-down version when generation_mode is balanced.' in prompt
    assert 'Do not create overpowered races.' not in prompt
    assert 'scale them down into balanced versions' not in prompt
    assert 'commonFeatures' in prompt
    assert 'Do not return null values' in prompt
    assert 'Descriptions must be complete sentences' in prompt
    assert 'trigger, duration, effects, limits, drawbacks, and endCondition' in prompt
    assert 'Do not leave transformation mechanics as scaling:null.' in prompt
    assert 'Do not add the flying tag unless the race has innate wings' in prompt


def test_custom_race_balanced_prompt_is_explicit_second_pass():
    prompt = _build_custom_race_prompt(
        'Create a battle-born custom race.',
        'standard',
        'balanced',
        {'name': 'Saiyan', 'traits': [{'name': 'Battle Surge', 'balanceCost': 8}]},
    )

    assert 'Generation mode: balanced.' in prompt
    assert 'Current canon-first draft to revise:' in prompt
    assert 'create a balanced playable variant' in prompt
    assert 'may deliberately downscale' in prompt


def test_custom_race_helper_payload_strips_null_metadata_and_preserves_ability_notes():
    long_hint = (
        'Great Ape is a campaign-defining transformation with collateral danger, '
        'loss of control, a full moon trigger, Blutz Wave trigger, exhaustion, '
        'tail severing as a countermeasure, ally risk, and battlefield scale.'
    )
    race = _race_payload_from_helper(
        {
            'name': 'Saiyan',
            'traits': [
                {
                    'id': 'great_ape_transformation',
                    'name': 'Great Ape Transformation',
                    'description': 'Transform into a giant ape under full moon or Blutz Wave exposure.',
                    'category': 'active_ability',
                    'balanceCost': 8,
                    'mechanics': {
                        'activeAbility': {
                            'actionType': 'reaction',
                            'cooldown': 'once_per_long_rest',
                            'effectType': 'transformation',
                            'scaling': None,
                            'effects': ['Size becomes Huge.', 'Gain 50 temporary hit points.'],
                        }
                    },
                    'aiHint': long_hint,
                }
            ],
        },
        'Create a Saiyan.',
    )

    active = race['traits'][0]['mechanics']['activeAbility']
    assert 'scaling' not in active
    assert race['traits'][0]['aiHint'] == long_hint


def test_race_registry_lists_curated_races_and_full_definition(client):
    response = client.get('/api/races')
    assert response.status_code == 200

    races = response.get_json()['races']
    names = {race['name'] for race in races}
    assert len(races) >= 31
    assert {'Dragonborn', 'Aarakocra', 'Warforged', 'Afro-Diasporic Human'}.issubset(names)

    dragonborn_response = client.get('/api/races/dragonborn')
    assert dragonborn_response.status_code == 200
    dragonborn = dragonborn_response.get_json()
    assert dragonborn['id'] == 'dragonborn'
    assert dragonborn['source'] == 'curated'
    assert dragonborn['visual']['portraitKey'] == 'dragonborn'
    assert dragonborn['physical'] == {'averageHeight': '6 to 7 feet', 'averageWeight': '220 to 320 lb'}
    assert dragonborn['languages'] == ['Common', 'Draconic']
    assert 'Intimidation' in dragonborn['commonProficiencies']
    assert dragonborn['descriptionShort'].startswith('Draconic humanoids whose scales')
    assert [trait['name'] for trait in dragonborn['traits']] == ['Breath Weapon', 'Elemental Resistance']
    assert dragonborn['balance']['tier'] == 'standard'

    fairy_response = client.get('/api/races/fairy')
    assert fairy_response.status_code == 200
    fairy = fairy_response.get_json()
    assert fairy['physical']['averageHeight'] == '2 to 3 feet'
    assert fairy['languages'] == ['Common', 'Sylvan']
    assert 'Flight' in [trait['name'] for trait in fairy['traits']]

    afro_diasporic_response = client.get('/api/races/afro-diasporic-human')
    assert afro_diasporic_response.status_code == 200
    afro_diasporic = afro_diasporic_response.get_json()
    assert afro_diasporic['source'] == 'curated'
    assert afro_diasporic['visual']['portraitKey'] == 'afro-diasporic-human'
    assert [trait['name'] for trait in afro_diasporic['traits']] == ['Adaptable', 'Versatile', 'Diaspora Ties']
    assert 'African diaspora fantasy imagery' in afro_diasporic['descriptionLong']


def test_curated_race_relationships_are_mostly_catalog_races(client):
    response = client.get('/api/races')
    assert response.status_code == 200

    races = response.get_json()['races']
    catalog_names = {race['name'].lower() for race in races if race['source'] == 'curated'}

    def catalog_reference_count(values):
        return sum(
            1
            for value in values
            if any(name in str(value).lower() for name in catalog_names)
        )

    for race in races:
        if race['source'] != 'curated':
            continue
        assert len(race['friendlyWith']) == 5, race['name']
        assert len(race['waryOf']) == 5, race['name']
        assert catalog_reference_count(race['friendlyWith']) >= 2, race['name']
        assert catalog_reference_count(race['waryOf']) >= 2, race['name']


def test_custom_race_generation_save_and_versioning(client):
    generate_response = client.post(
        '/api/custom-races/generate',
        json={
            'prompt': (
                'I want a race called Emberborn. They descend from fire spirits, '
                'have glowing veins, resist heat, and once per rest release flame.'
            ),
            'strictness': 'standard',
        },
    )
    assert generate_response.status_code == 200
    generated = generate_response.get_json()
    draft = generated['draftRace']
    assert draft['name'] == 'Emberborn'
    assert draft['source'] == 'custom'
    assert [trait['name'] for trait in draft['traits']] == ['Fire Resistance', 'Ember Burst']
    assert 'flying' not in draft['tags']
    assert 'wings' not in draft['visual']['commonFeatures']
    assert generated['balanceAnalysis']['tier'] == 'standard'
    assert generated['generationSource'] == 'deterministic'
    assert generated['generationMode'] == 'canon'

    save_response = client.post(
        '/api/custom-races',
        json={'raceDefinition': draft, 'approvalStatus': 'approved_by_user'},
    )
    assert save_response.status_code == 201
    saved = save_response.get_json()['race']
    assert saved['version'] == 1
    assert saved['approvalStatus'] == 'approved_by_user'
    assert saved['physical']['averageHeight'] == 'Varies by concept'
    assert saved['languages'] == ['Common']

    list_response = client.get('/api/races?source=custom')
    assert list_response.status_code == 200
    assert [race['id'] for race in list_response.get_json()['races']] == [saved['id']]

    patch_response = client.patch(
        f"/api/custom-races/{saved['id']}",
        json={'descriptionShort': 'Fire-spirit descendants with balanced flame gifts.'},
    )
    assert patch_response.status_code == 200
    updated = patch_response.get_json()['race']
    assert updated['version'] == 2
    assert updated['descriptionShort'] == 'Fire-spirit descendants with balanced flame gifts.'

    full_response = client.get(f"/api/races/{saved['id']}")
    assert full_response.status_code == 200
    assert full_response.get_json()['version'] == 2


def test_custom_race_catalog_is_global_and_includes_creator(client, app):
    with app.app_context():
        account = Account(
            username='aidan',
            first_name='Aidan',
            last_name='Fernandez',
        )
        db.session.add(account)
        db.session.flush()
        race_definition = {
            'id': 'custom_global_saiyan',
            'version': 1,
            'name': 'Saiyan',
            'source': 'custom',
            'descriptionShort': 'Warrior aliens with tails and explosive battle growth.',
            'descriptionLong': 'Canon-first custom Saiyan race.',
            'aliases': ['saiyan'],
            'tags': ['martial', 'exotic'],
            'size': 'medium',
            'baseSpeed': 30,
            'visual': {
                'portraitKey': 'saiyan_portrait',
                'iconKey': 'saiyan_icon',
                'bodyType': 'humanoid_muscular',
                'commonFeatures': ['tail', 'spiky hair'],
            },
            'physical': {'averageHeight': '5 to 6 feet', 'averageWeight': '140 to 230 lb'},
            'languages': ['Common'],
            'commonProficiencies': ['Athletics'],
            'traits': [
                {
                    'id': 'great_ape_transformation',
                    'name': 'Great Ape Transformation',
                    'description': 'Transform under full moon or Blutz Waves.',
                    'category': 'active_ability',
                    'mechanics': {'activeAbility': {'effectType': 'transformation'}},
                    'aiHint': 'Treat this as a campaign-defining form.',
                    'balanceCost': 8,
                }
            ],
            'roleplayHooks': ['Always seeks stronger opponents.'],
            'recommendedClasses': ['Fighter'],
            'difficulty': 'advanced',
            'balance': {'budget': 5, 'spent': 8, 'tier': 'strong'},
        }
        db.session.add(
            CustomRace(
                workspace_id='aidan_test',
                account_id=account.account_id,
                creator_username=account.username,
                creator_display_name='Aidan Fernandez',
                race_id='custom_global_saiyan',
                version=1,
                name='Saiyan',
                approval_status='approved_by_user',
                race_definition=safe_json_dumps(race_definition, {}),
            )
        )
        db.session.commit()

    list_response = client.get('/api/races?source=custom')
    assert list_response.status_code == 200
    races = list_response.get_json()['races']
    assert [race['id'] for race in races] == ['custom_global_saiyan']
    assert races[0]['workspaceId'] == 'aidan_test'
    assert races[0]['createdByUsername'] == 'aidan'
    assert races[0]['createdByDisplayName'] == 'Aidan Fernandez'

    full_response = client.get('/api/races/custom_global_saiyan')
    assert full_response.status_code == 200
    full_race = full_response.get_json()
    assert full_race['name'] == 'Saiyan'
    assert full_race['createdByUsername'] == 'aidan'

    exact_response = client.get('/api/races/custom_global_saiyan?workspaceId=aidan_test')
    assert exact_response.status_code == 200
    assert exact_response.get_json()['workspaceId'] == 'aidan_test'
