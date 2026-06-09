# __init__.py in blueprints

from .accounts import accounts_bp
from .campaigns import campaigns_bp
from .worlds import worlds_bp
from .players import players_bp
from .races import races_bp
from .sessions import sessions_bp
from .segments import segments_bp  # <-- NEW

__all__ = [
    'accounts_bp',
    'campaigns_bp',
    'worlds_bp',
    'players_bp',
    'races_bp',
    'sessions_bp',
    'segments_bp'
]
