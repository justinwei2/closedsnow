"""Snow physics heuristics: phase, snow level, snow-to-liquid ratio (SLR), quality labels.

Constants and their provenance (see web/about.html):
- Rain/snow: logistic in wet-bulb temp, 50% at TW50. Jennings et al. 2018 (Nat. Commun.)
  put the 50% *air*-temperature threshold at ~0.6-1.5 C for the Sierra/Pacific coast; wet-bulb
  sits below air temp when unsaturated, so 0.5 C wet-bulb is a moderate choice. Tunable.
- SLR: Kuchera-style ramp on the warmest temperature between the elevation and 500 hPa.
  Standard implementation form; constants not re-verified against the 2005 paper.
- Wind penalty and density classes: heuristics, unverified.
"""
import math

TW50, TW_SCALE = 0.5, 0.35      # snow fraction 50% at Tw=0.5C; ~90% at -0.3C, ~10% at 1.3C
KUCHERA_T0 = 271.16             # K
SLR_MIN, SLR_MAX = 4.0, 25.0
WARM_NOSE_C = 2.0               # layer aloft warmer than this over sub-freezing surface => sleet/ZR


def wet_bulb(t, rh):
    """Stull (2011) wet-bulb approximation, deg C. Valid ~5-99% RH, -20..50 C."""
    rh = min(max(rh, 5.0), 99.0)
    return (t * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(t + rh)
            - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def snow_fraction(tw):
    return 1.0 / (1.0 + math.exp((tw - TW50) / TW_SCALE))


def interp(z, zs, vs):
    """Linear interpolation of vs at height z; zs ascending. Extrapolate flat."""
    if z <= zs[0]:
        return vs[0]
    for i in range(1, len(zs)):
        if z <= zs[i]:
            f = (z - zs[i - 1]) / (zs[i] - zs[i - 1])
            return vs[i - 1] + f * (vs[i] - vs[i - 1])
    return vs[-1]


class Profile:
    """One hour's sounding: heights (m, ascending), temp (C), rh (%), wind (km/h)."""

    def __init__(self, z, t, rh, wind):
        order = sorted(range(len(z)), key=lambda i: z[i])
        self.z = [z[i] for i in order]
        self.t = [t[i] for i in order]
        self.rh = [rh[i] for i in order]
        self.w = [wind[i] for i in order]
        self.tw = [wet_bulb(a, b) for a, b in zip(self.t, self.rh)]

    def at(self, z):
        return dict(t=interp(z, self.z, self.t), rh=interp(z, self.z, self.rh),
                    tw=interp(z, self.z, self.tw), wind=interp(z, self.z, self.w))

    def crossing(self, values, thresh):
        """Highest height where `values` crosses down through thresh (going up). None if all below."""
        best = None
        for i in range(1, len(self.z)):
            a, b = values[i - 1], values[i]
            if (a - thresh) * (b - thresh) <= 0 and a != b:
                best = self.z[i - 1] + (thresh - a) / (b - a) * (self.z[i] - self.z[i - 1])
        if best is None and values[0] < thresh:
            return self.z[0]  # whole column below threshold: level is below the lowest level
        return best

    def freezing_level(self):
        return self.crossing(self.t, 0.0)

    def snow_level(self):
        """Height of 50% snow fraction (wet-bulb = TW50)."""
        return self.crossing(self.tw, TW50)

    def column_tmax(self, z):
        ts = [self.at(z)["t"]] + [t for zz, t in zip(self.z, self.t) if zz > z]
        return max(ts)

    def warm_nose(self, z):
        """Classic sleet/freezing-rain sounding: snow grows aloft (T < -5 C above the warm layer),
        melts in a layer warmer than WARM_NOSE_C, then falls into sub-freezing air at z."""
        if self.at(z)["tw"] > 0.0:
            return False
        above = [(zz, t) for zz, t in zip(self.z, self.t) if zz > z + 100]
        for k, (zz, t) in enumerate(above):
            if t > WARM_NOSE_C:
                return any(t2 < -5.0 for _, t2 in above[k + 1:])
        return False


def kuchera_slr(tmax_c):
    tk = tmax_c + 273.15
    slr = 12 + 2 * (KUCHERA_T0 - tk) if tk > KUCHERA_T0 else 12 + (KUCHERA_T0 - tk)
    return slr


def wind_penalty(wind_kmh):
    """Crystal fragmentation in strong wind packs snow denser. Heuristic: -2.5%/10 km/h over 30."""
    over = max(0.0, wind_kmh - 30.0)
    return max(0.7, 1.0 - 0.0025 * over)


def slr_at(profile, z):
    slr = kuchera_slr(profile.column_tmax(z)) * wind_penalty(profile.at(z)["wind"])
    return min(SLR_MAX, max(SLR_MIN, slr))


def quality(slr, frac, warm_nose=False):
    """Skier-facing label for snow falling at an elevation."""
    if frac < 0.2:
        return "rain"
    if warm_nose:
        return "sleet / freezing rain risk"
    if frac < 0.8:
        return "mixed rain/snow"
    dens = 100.0 / slr
    if dens < 6:
        return "blower"
    if dens < 8:
        return "light & dry"
    if dens < 11:
        return "medium"
    if dens < 15:
        return "dense"
    return "heavy / wet (Sierra cement)"
