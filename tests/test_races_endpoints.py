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


def test_custom_race_returns_only_explicit_public_validation_message(client, monkeypatch):
    import aidm_server.blueprints.races as races_blueprint
    from aidm_server.race_system import RaceValidationError

    internal_detail = '/srv/private/races.json token=secret-race-token'

    class HostileRaceValidationError(RaceValidationError):
        def __str__(self):
            return internal_detail

    def fail_normalization(*args, **kwargs):
        del args, kwargs
        raise HostileRaceValidationError('Race definition is invalid.')

    monkeypatch.setattr(races_blueprint, 'normalize_race_definition', fail_normalization)

    response = client.post('/api/custom-races', json={'raceDefinition': {}})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload['error_code'] == 'validation_error'
    assert payload['error'] == 'Race definition is invalid.'
    assert internal_detail not in str(payload)


def test_custom_race_rejects_malformed_numeric_fields_as_public_validation_errors(client):
    for field in ('version', 'baseSpeed'):
        response = client.post(
            '/api/custom-races',
            json={'raceDefinition': {'name': 'Malformed Numerics', field: 'not-an-integer'}},
        )
        payload = response.get_json()

        assert response.status_code == 400
        assert response.content_type == 'application/json'
        assert payload['error_code'] == 'validation_error'
        assert payload['error'] == f'raceDefinition.{field} must be an integer.'

        overflow_response = client.post(
            '/api/custom-races',
            data=f'{{"raceDefinition":{{"name":"Overflow Numerics","{field}":1e400}}}}',
            content_type='application/json',
        )
        overflow_payload = overflow_response.get_json()

        assert overflow_response.status_code == 400
        assert overflow_response.content_type == 'application/json'
        assert overflow_payload['error_code'] == 'validation_error'
        assert overflow_payload['error'] == f'raceDefinition.{field} must be an integer.'


def test_custom_race_catalog_is_scoped_to_request_workspace(client, app):
    app.config['AIDM_AUTH_REQUIRED'] = True
    app.config['AIDM_API_AUTH_TOKENS'] = ['attacker-token', 'victim-token']
    app.config['AIDM_API_AUTH_TOKEN_WORKSPACES'] = {
        'attacker-token': 'attacker-ws',
        'victim-token': 'victim-ws',
    }
    with app.app_context():
        victim_account = Account(
            username='victim_user',
            first_name='Victim',
            last_name='GM',
        )
        attacker_account = Account(
            username='attacker_user',
            first_name='Attacker',
            last_name='Player',
        )
        db.session.add_all([victim_account, attacker_account])
        db.session.flush()
        victim_race_definition = {
            'id': 'secret_moonfolk',
            'version': 1,
            'name': 'Secret Moonfolk',
            'source': 'custom',
            'descriptionShort': 'Victim-only lunar heritage.',
            'descriptionLong': 'VICTIM SECRET full definition includes campaign-specific Castle Umbra prompt.',
            'aliases': ['moonfolk'],
            'tags': ['mystical', 'exotic'],
            'size': 'medium',
            'baseSpeed': 30,
            'visual': {
                'portraitKey': 'moonfolk_portrait',
                'iconKey': 'moonfolk_icon',
                'bodyType': 'lithe_humanoid',
                'commonFeatures': ['silver eyes', 'moonlit markings'],
            },
            'physical': {'averageHeight': '5 to 6 feet', 'averageWeight': '140 to 230 lb'},
            'languages': ['Common'],
            'commonProficiencies': ['Insight'],
            'traits': [
                {
                    'id': 'moon_veil',
                    'name': 'Moon Veil',
                    'description': 'Blend with pale moonlight.',
                    'category': 'active_ability',
                    'mechanics': {'activeAbility': {'effectType': 'stealth_boost'}},
                    'aiHint': 'Use only in the victim campaign context.',
                    'balanceCost': 3,
                }
            ],
            'roleplayHooks': ['Protects the Castle Umbra secret.'],
            'recommendedClasses': ['Rogue'],
            'difficulty': 'medium',
            'balance': {'budget': 5, 'spent': 3, 'tier': 'standard'},
        }
        attacker_race_definition = {
            **victim_race_definition,
            'id': 'attacker_drakekin',
            'name': 'Attacker Drakekin',
            'descriptionShort': 'Attacker workspace race.',
            'descriptionLong': 'Attacker workspace full definition.',
            'aliases': ['drakekin'],
            'roleplayHooks': ['Tests workspace isolation.'],
        }
        db.session.add_all([
            CustomRace(
                workspace_id='victim-ws',
                account_id=victim_account.account_id,
                creator_username=victim_account.username,
                creator_display_name='Victim GM',
                race_id='secret_moonfolk',
                version=1,
                name='Secret Moonfolk',
                approval_status='approved_by_user',
                race_definition=safe_json_dumps(victim_race_definition, {}),
            ),
            CustomRace(
                workspace_id='attacker-ws',
                account_id=attacker_account.account_id,
                creator_username=attacker_account.username,
                creator_display_name='Attacker Player',
                race_id='attacker_drakekin',
                version=1,
                name='Attacker Drakekin',
                approval_status='approved_by_user',
                race_definition=safe_json_dumps(attacker_race_definition, {}),
            ),
        ])
        db.session.commit()

    assert client.get('/api/races?source=custom').status_code == 401

    attacker_headers = {'X-AIDM-Workspace-Token': 'attacker-token'}
    list_response = client.get('/api/races?source=custom', headers=attacker_headers)
    assert list_response.status_code == 200
    races = list_response.get_json()['races']
    assert [race['id'] for race in races] == ['attacker_drakekin']
    assert races[0]['workspaceId'] == 'attacker-ws'
    assert races[0]['createdByUsername'] == 'attacker_user'
    assert races[0]['createdByDisplayName'] == 'Attacker Player'
    assert 'secret_moonfolk' not in {race['id'] for race in races}
    assert 'Victim' not in str(races)

    full_response = client.get('/api/races/attacker_drakekin', headers=attacker_headers)
    assert full_response.status_code == 200
    full_race = full_response.get_json()
    assert full_race['workspaceId'] == 'attacker-ws'
    assert full_race['createdByUsername'] == 'attacker_user'

    exact_attacker_response = client.get(
        '/api/races/attacker_drakekin?workspaceId=attacker-ws',
        headers=attacker_headers,
    )
    assert exact_attacker_response.status_code == 200
    assert exact_attacker_response.get_json()['workspaceId'] == 'attacker-ws'

    hidden_response = client.get('/api/races/secret_moonfolk', headers=attacker_headers)
    assert hidden_response.status_code == 404
    hidden_exact_response = client.get(
        '/api/races/secret_moonfolk?workspaceId=victim-ws',
        headers=attacker_headers,
    )
    assert hidden_exact_response.status_code == 404
    mismatched_workspace_response = client.get(
        '/api/races/attacker_drakekin?workspaceId=victim-ws',
        headers=attacker_headers,
    )
    assert mismatched_workspace_response.status_code == 404

    victim_headers = {'X-AIDM-Workspace-Token': 'victim-token'}
    victim_response = client.get('/api/races/secret_moonfolk', headers=victim_headers)
    assert victim_response.status_code == 200
    victim_race = victim_response.get_json()
    assert victim_race['workspaceId'] == 'victim-ws'
    assert victim_race['descriptionLong'].startswith('VICTIM SECRET')
