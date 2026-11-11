from __future__ import annotations

from flask import Blueprint, jsonify

from aidm_server.creation_options import creation_options_payload


rules_bp = Blueprint('rules', __name__)


@rules_bp.get('/rules/creation-options')
def get_creation_options():
    """What a level-1 hero is made under: core classes and backgrounds."""

    return jsonify(creation_options_payload())
