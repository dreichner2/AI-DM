"""A lone ready-made hero can win the Play Now fights under party scaling."""

from __future__ import annotations

from scripts.solo_balance import ROUTE_XP, win_rates


def test_a_lone_fighter_or_wizard_wins_every_play_now_fight_often_enough():
    rates = win_rates(['arden-vale', 'liora-quill'], seeds=12)

    assert set(rates) == {'arden-vale', 'liora-quill'}
    assert {encounter_id for hero in rates.values() for encounter_id in hero} == {encounter_id for encounter_id, _xp in ROUTE_XP}
    assert {
        (hero, encounter_id): rate
        for hero, by_encounter in rates.items()
        for encounter_id, rate in by_encounter.items()
        if rate < 0.4
    } == {}
