"""
Strategies package — widget data providers for ActiveScan.

WIDGET_MAP is the single source of truth used by routes/scan.py to
look up strategy widgets by slug.
"""

from .regime_momentum import RegimeMomentumWidget

WIDGETS: list = [
    RegimeMomentumWidget(),
]

WIDGET_MAP: dict = {w.slug: w for w in WIDGETS}
