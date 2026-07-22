"""First fall frost — probability layer (Phase 1 launch layer).

Per slider date D: P(first fall freeze, tmin ≤ 0°C, has occurred on or before D).

Climatology: a first freeze is an extreme-value event — the first cold NIGHT —
which lands weeks before the smooth 30-yr mean tmin curve reaches 0°C, so
mean-curve crossing is the wrong model (it biased ~3-4 weeks late and marked
the Southeast "never"). Instead, from the PRISM daily-interpolated normal
tmin curve we model daily variability around the normal as Gaussian
(σ_daily, °C) and accumulate the freeze hazard through the season:

    p_d   = Φ((0 − tmin_normal_d) / σ_daily)      # freeze chance on day d
    S(D)  = Π_{d ≤ D} (1 − p_d)                   # survival (no freeze yet)
    CDF(D) = 1 − S(D)

The CDF is then summarized per cell by its median date μ50 and an effective
spread σ_eff (from the 16th percentile), and served as Φ((doy − μ50)/σ_eff).
σ_daily is calibrated against published NWS/almanac medians for the five
acceptance cities (day independence slightly inflates hazard; σ_daily absorbs
that too).

Forecast blend: Open-Meteo tmin for past_days..+10. If the forecast/analysis
freezes by D (any day k ≤ D with tmin ≤ 0°C), that's near-certain knowledge:
    P = max(P_climo, w(k_first) · 1)
with confidence w = 1.0 through lead day 7, decaying linearly to 0.6 at day 10
(per the build plan: forecast confidence decays after day 7).

Mirrored last-spring-frost product activates Feb–May (Phase 1.5; the math is
this module with the time axis reversed).

Cells whose normals never cross 0°C in fall (South Florida, coastal CA) carry
NaN μ → probability 0, and NODATA in the expected-date layer.
"""
from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple

import numpy as np

from ..core import grid
from ..core.sources import open_meteo, prism
from ..core.sources.elevation import static_dir
from .base import LayerOutput

LAYER = "frost"
IMPLEMENTED = True

SIGMA_DAILY_C = 3.75    # day-to-day tmin spread around the normal (°C);
                        # calibrated against published NWS/almanac medians for
                        # the 5 acceptance cities → worst error 4 days.
SIGMA_EFF_MIN = 4.0     # floor/cap for the fitted date spread (days)
SIGMA_EFF_MAX = 30.0
SEASON_START_DOY = 196  # Jul 15 — fall freeze season hazard window start

# NWS-style event thresholds (2m air temp). Frost can form at air temps above
# freezing via radiational surface cooling; hard freeze ends the growing season.
THRESHOLDS_C = {
    "frost": 2.2222,        # 36°F
    "freeze": 0.0,          # 32°F
    "hard_freeze": -2.2222, # 28°F
}

_forecast_memo: dict = {}
_climo_memo: dict = {}


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    """Vectorized standard normal CDF (tanh approximation, |err| < 2e-3)."""
    z = np.sqrt(2.0 / np.pi) * (x + 0.044715 * x**3)
    return 0.5 * (1.0 + np.tanh(z))


def ensure_climatology(force: bool = False, threshold_c: float = 0.0):
    """(μ50, σ_eff) rasters for a given event threshold: median first fall
    event DOY and effective date spread per cell (float32; μ50 NaN = event
    reaches <50% odds by Dec 31)."""
    key = f"{threshold_c:+.2f}"
    if key in _climo_memo and not force:
        return _climo_memo[key]
    cache = static_dir() / f"frost_climo_v2_{key}.npz"
    if cache.exists() and not force:
        z = np.load(cache)
        _climo_memo[key] = (z["mu50"], z["sigma_eff"])
        return _climo_memo[key]

    stack = prism.ensure_tmin_normals()
    conus = stack[0] != grid.NODATA

    survival = np.ones(grid.SHAPE, dtype=np.float64)
    d16 = np.full(grid.SHAPE, np.nan, dtype=np.float32)
    mu50 = np.full(grid.SHAPE, np.nan, dtype=np.float32)
    for doy in range(SEASON_START_DOY, 366):
        tmin = prism.daily_tmin_normal(stack, doy)
        hazard = np.where(
            tmin != grid.NODATA, _norm_cdf((threshold_c - tmin) / SIGMA_DAILY_C), 0.0
        )
        survival *= 1.0 - hazard
        cdf = 1.0 - survival
        d16[np.isnan(d16) & (cdf >= 0.1587) & conus] = doy
        mu50[np.isnan(mu50) & (cdf >= 0.5) & conus] = doy

    sigma_eff = np.clip(mu50 - d16, SIGMA_EFF_MIN, SIGMA_EFF_MAX).astype(np.float32)
    sigma_eff[np.isnan(mu50)] = SIGMA_EFF_MAX
    np.savez_compressed(cache, mu50=mu50, sigma_eff=sigma_eff)
    _climo_memo[key] = (mu50, sigma_eff)
    return _climo_memo[key]


def _doy_of(date: str, season_year: int) -> float:
    """Day-of-year relative to the season year (continues past 365 in Jan)."""
    d = dt.date.fromisoformat(date)
    return float((d - dt.date(season_year, 1, 1)).days + 1)


def _season_year(run_date: str) -> int:
    d = dt.date.fromisoformat(run_date)
    # Jan runs still describe the fall season that just ended.
    return d.year - 1 if d.month == 1 else d.year


def _forecast_frozen_by(date: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """(frozen_by_D mask, confidence w) from the shared Open-Meteo fetch."""
    today = dt.date.today().isoformat()
    if today not in _forecast_memo:
        try:
            dates, fields = open_meteo.fetch_daily_fields(today)
            _forecast_memo[today] = (dates, fields["temperature_2m_min"])
        except Exception:  # noqa: BLE001 — forecast optional, climo carries
            _forecast_memo[today] = None
    memo = _forecast_memo[today]
    if memo is None:
        return None
    dates, tmin = memo
    if date < dates[0]:
        return None

    run_idx = dates.index(today) if today in dates else open_meteo.PAST_DAYS
    frozen = np.zeros(grid.SHAPE, dtype=bool)
    w = np.zeros(grid.SHAPE, dtype=np.float32)
    for k, dk in enumerate(dates):
        if dk > date:
            break
        day = tmin[k]
        newly = (day != grid.NODATA) & (day <= 0.0) & ~frozen
        lead = max(0, k - run_idx)
        conf = 1.0 if lead <= 7 else max(0.6, 1.0 - (lead - 7) * (0.4 / 3.0))
        frozen |= newly
        w[newly] = conf
    return frozen, w


def dates_for(run_date: str) -> List[str]:
    """Daily through the forecast window, then weekly climatology-only
    checkpoints to the end of the fall season — the slider sweeps the whole
    season, not just 10 days."""
    base = dt.date.fromisoformat(run_date)
    dates = [(base + dt.timedelta(days=k)).isoformat() for k in range(-3, 8)]
    season_end = dt.date(_season_year(run_date), 12, 31)
    step = base + dt.timedelta(days=14)
    while step <= season_end:
        dates.append(step.isoformat())
        step += dt.timedelta(days=7)
    return dates


def score(date: str) -> LayerOutput:
    season = _season_year(date)
    mu50, sigma_eff = ensure_climatology()

    doy = _doy_of(date, season)
    p = _norm_cdf((doy - mu50) / sigma_eff)
    p = np.where(np.isnan(mu50), 0.0, p).astype(np.float32)

    fc = _forecast_frozen_by(date)
    if fc is not None:
        frozen, w = fc
        p = np.maximum(p, np.where(frozen, w, 0.0)).astype(np.float32)

    conus = prism.ensure_tmin_normals()[0] != grid.NODATA
    values = np.where(conus, p, grid.NODATA).astype(np.float32)

    return LayerOutput(
        layer=LAYER,
        date=date,
        values=values,
        colormap="frost_prob",
        vmin=0.0,
        vmax=1.0,
        units="probability",
        description=(
            "Probability the first fall freeze (tmin ≤ 32°F) has occurred by the "
            "slider date — daily steps carry the 10-day forecast, weekly steps "
            "beyond it are climatology"
        ),
        extra_meta={"value_format": "probability", "sigma_daily_c": SIGMA_DAILY_C},
    )
