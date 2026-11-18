"""Chargement réel de `config/desk.yaml`.

L'ancien `config/desk.yaml` n'était jamais lu par le code : le README
l'admettait (« the runtime is the source of truth »). Inacceptable dès que ce
fichier porte `dry_run` et les seuils de kill switch — ces valeurs ne doivent
pas vivre en dur dans le code.

Parseur YAML minimal intégré (sous-ensemble suffisant pour ce fichier) afin de
préserver la propriété « le noyau tourne en stdlib pure ». Si PyYAML est
installé, il est utilisé en priorité.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _ROOT / "config" / "desk.yaml"


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def _coerce(raw: str) -> Any:
    s = raw.strip()
    if s in ("~", "null", ""):
        return None
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p) for p in inner.split(",")]
    if s.startswith(("'", '"')) and s[-1:] == s[0]:
        return s[1:-1]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _mini_yaml(text: str) -> dict:
    """Mappings imbriqués par indentation + scalaires + listes inline.

    Volontairement limité : pas d'ancres, pas de listes par tirets, pas de
    multi-ligne. Si la config dépasse ce cadre, installer PyYAML.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        # Règle YAML : un commentaire commence à un '#' précédé d'un blanc.
        # Découper sur tout '#' casserait une valeur comme `couleur: #fff`, et
        # se fier à l'absence de quotes casse sur une apostrophe française
        # dans le commentaire lui-même ("# 2 % de l'equity").
        cut = body.find(" #")
        if cut != -1:
            body = body[:cut].strip()
        if ":" not in body:
            raise ValueError(f"desk.yaml:{lineno}: ligne inattendue {line!r}")
        key, _, rest = body.partition(":")
        key, rest = key.strip(), rest.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"desk.yaml:{lineno}: indentation incohérente")
        parent = stack[-1][1]
        if rest == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _coerce(rest)
    return root


def _load_mapping(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return _mini_yaml(text)
    return yaml.safe_load(text) or {}


# --------------------------------------------------------------------------
# schéma
# --------------------------------------------------------------------------
@dataclass
class RiskConfig:
    max_stake_fraction: float = 0.02
    max_open_positions: int = 3
    max_entries_per_hour: int = 6
    kelly_fraction: float = 0.25
    daily_drawdown_stop: float = 0.06
    consecutive_red_days_stop: int = 3
    ledger_drift_tolerance: float = 0.01


@dataclass
class EdgeConfig:
    min_edge: float = 0.02
    min_payout: float = 0.70
    max_feed_age_s: float = 2.0
    min_horizon_s: float = 30.0
    max_horizon_s: float = 900.0
    min_samples_for_live: int = 600


@dataclass
class PriorConfig:
    vol_halflife_s: float = 120.0
    vol_floor_per_s: float = 1e-6
    drift_per_s: float = 0.0
    use_variance_drag: bool = True


@dataclass
class Config:
    dry_run: bool = True
    bankroll: float = 1000.0
    assets: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    risk: RiskConfig = field(default_factory=RiskConfig)
    edge: EdgeConfig = field(default_factory=EdgeConfig)
    prior: PriorConfig = field(default_factory=PriorConfig)
    raw: dict = field(default_factory=dict)

    @property
    def live(self) -> bool:
        return not self.dry_run

    def banner(self) -> str:
        return "  LIVE — ARGENT RÉEL  " if self.live else "  DRY RUN — PAPIER  "


def _fill(cls, data: dict | None):
    if not data:
        return cls()
    known = {f for f in cls.__dataclass_fields__}
    return cls(**{k: v for k, v in data.items() if k in known})


def load(path: str | os.PathLike | None = None) -> Config:
    """Charge la config. `GROKDESK_DRY_RUN=0` ne suffit pas à passer en live :
    le fichier doit aussi le déclarer. Il faut les deux, par sécurité."""
    p = Path(path) if path else DEFAULT_PATH
    data = _load_mapping(p) if p.exists() else {}
    cfg = Config(
        dry_run=bool(data.get("dry_run", True)),
        bankroll=float(data.get("bankroll", 1000.0)),
        assets=list(data.get("assets") or ["BTC", "ETH", "SOL"]),
        risk=_fill(RiskConfig, data.get("risk")),
        edge=_fill(EdgeConfig, data.get("edge")),
        prior=_fill(PriorConfig, data.get("prior")),
        raw=data,
    )
    env = os.getenv("GROKDESK_DRY_RUN")
    if env is not None and env not in ("0", "false", "False"):
        cfg.dry_run = True
    if cfg.live and env is None:
        # le fichier dit live mais l'environnement ne confirme pas → papier
        cfg.dry_run = True
    return cfg
