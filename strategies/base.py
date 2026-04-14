"""
Base classes for ActiveScan strategy widgets.

Every widget is:
  - A Python StrategyWidget subclass (strategies/<slug>.py) — data provider
  - A Jinja2 HTML fragment (templates/widgets/<slug>.html) — initial render
  - A global JS object in window.WidgetRegistry (static/js/widgets/<slug>.js) — interactivity

Widget lifecycle (driven by scan.js on the browser):
  1. Page load:   fetch /scan/widget/<slug>?instrument=X → innerHTML of slot
  2. Mount:       window.WidgetRegistry[slug].mount(container, socket, instrument)
  3. Poll:        JS calls /api/scan/data/<slug>?instrument=X every 8 s, updates DOM in-place
  4. Tick:        JS socket.on("tick") handler filters relevant SIDs, updates LTP in-place
  5. Unmount:     window.WidgetRegistry[slug].unmount() — clears interval + socket.off
  6. Instrument change: unmount all → re-fetch HTML → mount all with new instrument
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalResult:
    """Normalised signal output shared across all strategy widgets."""
    action:          str                  # BUY | NO_TRADE | WAIT
    direction:       Optional[str] = None # CE | PE | None
    instrument:      str = ""
    entry:           Optional[float] = None
    target:          Optional[float] = None
    sl:              Optional[float] = None
    regime:          str = "—"
    phase:           str = "—"
    health_score:    float = 0
    lin_score:       float = 0
    reason:          str = ""
    counter_reasons: list = field(default_factory=list)
    generated_at:    str = ""
    error:           Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "action":          self.action,
            "direction":       self.direction,
            "instrument":      self.instrument,
            "entry":           self.entry,
            "target":          self.target,
            "sl":              self.sl,
            "regime":          self.regime,
            "phase":           self.phase,
            "health_score":    self.health_score,
            "lin_score":       self.lin_score,
            "reason":          self.reason,
            "counter_reasons": self.counter_reasons,
            "generated_at":    self.generated_at,
            "error":           self.error,
        }


class StrategyWidget(ABC):
    """Abstract base for all scan-page strategy widgets."""

    slug:        str  # URL key, JS registry key, template filename stem
    name:        str  # display name shown in widget header
    icon:        str = "📊"
    description: str = ""

    @abstractmethod
    def initial_data(self, instrument: str, snapshot: dict) -> dict:
        """
        Called once when the widget HTML fragment is first rendered
        (GET /scan/widget/<slug>?instrument=X).

        snapshot: the raw dict from /api/dashboard/snapshot for this instrument.
        Returns a dict that becomes the Jinja2 context for templates/widgets/<slug>.html.
        """
        ...

    @abstractmethod
    def poll_data(self, instrument: str, snapshot: dict) -> dict:
        """
        Called on every JS poll cycle (GET /api/scan/data/<slug>?instrument=X, every 8 s).
        Returns JSON-serialisable dict. JS widget._update(container, data) handles the DOM.
        Can return the same data as initial_data or a lighter subset.
        """
        ...
