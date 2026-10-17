"""Unit conversions. The pipeline works in US units (°F, mph, acres)."""


def c_to_f(c):
    return c * 9 / 5 + 32


def f_to_c(f):
    return (f - 32) * 5 / 9


def kmh_to_mph(kmh):
    return kmh / 1.609344


def mph_to_kmh(mph):
    return mph * 1.609344
