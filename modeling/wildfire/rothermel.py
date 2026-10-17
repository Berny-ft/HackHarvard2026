"""Rothermel (1972) surface fire spread, with Albini (1976) refinements.

Fuel parameters are the Scott & Burgan (2005) 40 standard fire behavior fuel
models, i.e. what LANDFIRE's FBFM40 layer encodes. English units internally
(ft, lb, BTU, ft/min), as in the original papers.

Reference: Andrews, P.L. 2018. The Rothermel surface fire spread model and
associated developments. RMRS-GTR-371.
"""
import math

import numpy as np

T_AC_TO_LB_FT2 = 0.0459137
RHO_P, S_T, S_E, HEAT = 32.0, 0.0555, 0.010, 8000.0
SAV_10H, SAV_100H = 109.0, 30.0

# code: (name, 1h, 10h, 100h, live_herb, live_woody [t/ac],
#        sav_1h, sav_herb, sav_woody [1/ft], depth [ft], mx_dead [%], dynamic)
FUEL_MODELS = {
    101: ("GR1", 0.10, 0.00, 0.00, 0.30, 0.00, 2200, 2000, 9999, 0.4, 15, True),
    102: ("GR2", 0.10, 0.00, 0.00, 1.00, 0.00, 2000, 1800, 9999, 1.0, 15, True),
    103: ("GR3", 0.10, 0.40, 0.00, 1.50, 0.00, 1500, 1300, 9999, 2.0, 30, True),
    104: ("GR4", 0.25, 0.00, 0.00, 1.90, 0.00, 2000, 1800, 9999, 2.0, 15, True),
    105: ("GR5", 0.40, 0.00, 0.00, 2.50, 0.00, 1800, 1600, 9999, 1.5, 40, True),
    106: ("GR6", 0.10, 0.00, 0.00, 3.40, 0.00, 2200, 2000, 9999, 1.5, 40, True),
    107: ("GR7", 1.00, 0.00, 0.00, 5.40, 0.00, 2000, 1800, 9999, 3.0, 15, True),
    108: ("GR8", 0.50, 1.00, 0.00, 7.30, 0.00, 1500, 1300, 9999, 4.0, 30, True),
    109: ("GR9", 1.00, 1.00, 0.00, 9.00, 0.00, 1800, 1600, 9999, 5.0, 40, True),
    121: ("GS1", 0.20, 0.00, 0.00, 0.50, 0.65, 2000, 1800, 1800, 0.9, 15, True),
    122: ("GS2", 0.50, 0.50, 0.00, 0.60, 1.00, 2000, 1800, 1800, 1.5, 15, True),
    123: ("GS3", 0.30, 0.25, 0.00, 1.45, 1.25, 1800, 1600, 1600, 1.8, 40, True),
    124: ("GS4", 1.90, 0.30, 0.10, 3.40, 7.10, 1800, 1600, 1600, 2.1, 40, True),
    141: ("SH1", 0.25, 0.25, 0.00, 0.15, 1.30, 2000, 1800, 1600, 1.0, 15, True),
    142: ("SH2", 1.35, 2.40, 0.75, 0.00, 3.85, 2000, 9999, 1600, 1.0, 15, False),
    143: ("SH3", 0.45, 3.00, 0.00, 0.00, 6.20, 1600, 9999, 1400, 2.4, 40, False),
    144: ("SH4", 0.85, 1.15, 0.20, 0.00, 2.55, 2000, 1800, 1600, 3.0, 30, False),
    145: ("SH5", 3.60, 2.10, 0.00, 0.00, 2.90, 750, 9999, 1600, 6.0, 15, False),
    146: ("SH6", 2.90, 1.45, 0.00, 0.00, 1.40, 750, 9999, 1600, 2.0, 30, False),
    147: ("SH7", 3.50, 5.30, 2.20, 0.00, 3.40, 750, 9999, 1600, 6.0, 15, False),
    148: ("SH8", 2.05, 3.40, 0.85, 0.00, 4.35, 750, 9999, 1600, 3.0, 40, False),
    149: ("SH9", 4.50, 2.45, 0.00, 1.55, 7.00, 750, 1800, 1500, 4.4, 40, True),
    161: ("TU1", 0.20, 0.90, 1.50, 0.20, 0.90, 2000, 1800, 1600, 0.6, 20, True),
    162: ("TU2", 0.95, 1.80, 1.25, 0.00, 0.20, 2000, 9999, 1600, 1.0, 30, False),
    163: ("TU3", 1.10, 0.15, 0.25, 0.65, 1.10, 1800, 1600, 1400, 1.3, 30, True),
    164: ("TU4", 4.50, 0.00, 0.00, 0.00, 2.00, 2300, 9999, 2000, 0.5, 12, False),
    165: ("TU5", 4.00, 4.00, 3.00, 0.00, 3.00, 1500, 9999, 750, 1.0, 25, False),
    181: ("TL1", 1.00, 2.20, 3.60, 0.00, 0.00, 2000, 9999, 9999, 0.2, 30, False),
    182: ("TL2", 1.40, 2.30, 2.20, 0.00, 0.00, 2000, 9999, 9999, 0.2, 25, False),
    183: ("TL3", 0.50, 2.20, 2.80, 0.00, 0.00, 2000, 9999, 9999, 0.3, 20, False),
    184: ("TL4", 0.50, 1.50, 4.20, 0.00, 0.00, 2000, 9999, 9999, 0.4, 25, False),
    185: ("TL5", 1.15, 2.50, 4.40, 0.00, 0.00, 2000, 9999, 9999, 0.6, 25, False),
    186: ("TL6", 2.40, 1.20, 1.20, 0.00, 0.00, 2000, 9999, 9999, 0.3, 25, False),
    187: ("TL7", 0.30, 1.40, 8.10, 0.00, 0.00, 2000, 9999, 9999, 0.4, 25, False),
    188: ("TL8", 5.80, 1.40, 1.10, 0.00, 0.00, 1800, 9999, 9999, 0.3, 35, False),
    189: ("TL9", 6.65, 3.30, 4.15, 0.00, 0.00, 1800, 9999, 9999, 0.6, 35, False),
    201: ("SB1", 1.50, 3.00, 11.0, 0.00, 0.00, 2000, 9999, 9999, 1.0, 25, False),
    202: ("SB2", 4.50, 4.25, 4.00, 0.00, 0.00, 2000, 9999, 9999, 1.0, 25, False),
    203: ("SB3", 5.50, 2.75, 3.00, 0.00, 0.00, 2000, 9999, 9999, 1.2, 25, False),
    204: ("SB4", 5.25, 3.50, 5.25, 0.00, 0.00, 2000, 9999, 9999, 2.7, 25, False),
}


def _damping(m, mx):
    r = min(m / mx, 1.0) if mx > 0 else 1.0
    return max(0.0, 1 - 2.59 * r + 5.11 * r ** 2 - 3.52 * r ** 3)


def fuel_bed(code, m1, m10, m100, m_herb, m_woody):
    """No-wind, no-slope spread and the coefficients for wind/slope factors.

    Moistures are fractions (0.06 = 6%). Returns None for non-burnable codes.
    """
    if code not in FUEL_MODELS:
        return None
    _, w1, w10, w100, wh, ww, s1, sh, sw, depth, mx_pct, dynamic = FUEL_MODELS[code]
    mx = mx_pct / 100.0

    # Dynamic models: cured herbaceous load moves into the dead class.
    cured = 0.0
    if dynamic and wh > 0:
        cured = float(np.clip((1.20 - m_herb) / 0.90, 0, 1))
    dead = [  # (load lb/ft2, sav, moisture)
        (w1 * T_AC_TO_LB_FT2, s1, m1),
        (w10 * T_AC_TO_LB_FT2, SAV_10H, m10),
        (w100 * T_AC_TO_LB_FT2, SAV_100H, m100),
        (wh * cured * T_AC_TO_LB_FT2, sh, m1),
    ]
    live = [
        (wh * (1 - cured) * T_AC_TO_LB_FT2, sh, m_herb),
        (ww * T_AC_TO_LB_FT2, sw, m_woody),
    ]
    dead = [p for p in dead if p[0] > 0]
    live = [p for p in live if p[0] > 0]

    def area(ps):
        return [w * s / RHO_P for w, s, _ in ps]

    a_d, a_l = area(dead), area(live)
    A_d, A_l = sum(a_d), sum(a_l)
    A_t = A_d + A_l
    cats = []
    for ps, a, A in ((dead, a_d, A_d), (live, a_l, A_l)):
        if A <= 0:
            cats.append(None)
            continue
        f = [x / A for x in a]
        cats.append({
            "f_cat": A / A_t,
            "sigma": sum(fi * s for fi, (_, s, _) in zip(f, ps)),
            "w_n": sum(fi * w * (1 - S_T) for fi, (w, _, _) in zip(f, ps)),
            "m": sum(fi * m for fi, (_, _, m) in zip(f, ps)),
            "qig": sum(fi * math.exp(-138 / s) * (250 + 1116 * m) for fi, (_, s, m) in zip(f, ps)),
        })

    sigma = sum(c["f_cat"] * c["sigma"] for c in cats if c)
    w0 = sum(w for w, _, _ in dead + live)
    rho_b = w0 / depth
    beta = rho_b / RHO_P
    beta_op = 3.348 * sigma ** -0.8189
    ratio = beta / beta_op

    # Live fuel moisture of extinction (Albini 1976).
    mx_live = mx
    if live:
        fine_d = sum(w * math.exp(-138 / s) for w, s, _ in dead)
        fine_l = sum(w * math.exp(-500 / s) for w, s, _ in live)
        md_fine = sum(w * math.exp(-138 / s) * m for w, s, m in dead) / max(fine_d, 1e-12)
        if fine_l > 0:
            mx_live = max(mx, 2.9 * (fine_d / fine_l) * (1 - md_fine / mx) - 0.226)

    gmax = sigma ** 1.5 / (495 + 0.0594 * sigma ** 1.5)
    A = 133 * sigma ** -0.7913
    gamma = gmax * ratio ** A * math.exp(A * (1 - ratio))
    eta_s = min(1.0, 0.174 * S_E ** -0.19)
    ir = 0.0
    for c, m_ext in zip(cats, (mx, mx_live)):
        if c:
            ir += gamma * c["w_n"] * HEAT * _damping(c["m"], m_ext) * eta_s
    xi = math.exp((0.792 + 0.681 * sigma ** 0.5) * (beta + 0.1)) / (192 + 0.2595 * sigma)
    heat_sink = rho_b * sum(c["f_cat"] * c["qig"] for c in cats if c)
    r0 = ir * xi / heat_sink if heat_sink > 0 else 0.0

    return {
        "r0": r0,  # ft/min
        "ir": ir,  # BTU/ft2/min
        "C": 7.47 * math.exp(-0.133 * sigma ** 0.55),
        "B": 0.02526 * sigma ** 0.54,
        "E": 0.715 * math.exp(-3.59e-4 * sigma),
        "ratio": ratio,
        "beta": beta,
    }


def fosberg_emc(temp_f, rh):
    """Equilibrium moisture content (fraction) from Simard (1968)."""
    t, h = temp_f, rh
    emc = np.where(
        h < 10, 0.03229 + 0.281073 * h - 0.000578 * h * t,
        np.where(h < 50, 2.22749 + 0.160107 * h - 0.01478 * t,
                 21.0606 + 0.005565 * h ** 2 - 0.00035 * h * t - 0.483199 * h))
    return np.clip(emc, 1.0, 35.0) / 100.0
