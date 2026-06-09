from __future__ import annotations


def test_race_registry_lists_curated_races_and_full_definition(client):
    response = client.get('/api/races')
    assert response.status_code == 200

    races = response.get_json()['races']
    names = {race['name'] for race in races}
    assert len(races) >= 30
    assert {'Dragonborn', 'Aarakocra', 'Warforged'}.issubset(names)

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
