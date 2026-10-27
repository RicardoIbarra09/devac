import math
import random
from typing import Callable, List

PATTERNS = ("poisson", "uniform", "pico", "rafaga")


def poisson_times(rng: random.Random, rate: float, window: float) -> List[float]:
    times = []
    t = rng.expovariate(rate)
    while t < window:
        times.append(t)
        t += rng.expovariate(rate)
    return times


# poisson no homogeneo por adelgazamiento (lewis-shedler)
def _thinning(rng: random.Random, rate_at: Callable[[float], float], rate_max: float, window: float) -> List[float]:
    return [t for t in poisson_times(rng, rate_max, window) if rng.random() * rate_max < rate_at(t)]


# pico gaussiano a la mitad de la ventana
def _peak(rng, rate, window, peak_factor):
    center, width = window / 2, window / 8
    def rate_at(t):
        return rate * (1 + (peak_factor - 1) * math.exp(-((t - center) / width) ** 2))
    return _thinning(rng, rate_at, rate * max(peak_factor, 1.0), window)


# periodos on/off; la tasa media sigue siendo rate
def _bursts(rng, rate, window, burst_factor, burst_fraction, burst_len):
    if burst_factor * burst_fraction >= 1:
        raise ValueError("burst_factor * burst_fraction debe ser < 1 para conservar la tasa media")
    on_rate = rate * burst_factor
    off_rate = rate * (1 - burst_factor * burst_fraction) / (1 - burst_fraction)
    off_len = burst_len * (1 - burst_fraction) / burst_fraction

    times = []
    t, on = 0.0, rng.random() < burst_fraction
    while t < window:
        length = rng.expovariate(1 / (burst_len if on else off_len))
        end = min(t + length, window)
        times.extend(t + x for x in poisson_times(rng, on_rate if on else off_rate, end - t))
        t, on = end, not on
    return times


def arrival_times(pattern: str, rate: float, window: float, rng: random.Random, *,
                  peak_factor: float = 3.0, burst_factor: float = 4.0,
                  burst_fraction: float = 0.2, burst_len: float = 1.0) -> List[float]:
    if rate <= 0 or window <= 0:
        return []
    if pattern == "poisson":
        return poisson_times(rng, rate, window)
    if pattern == "uniform":
        return [i / rate for i in range(math.ceil(window * rate)) if i / rate < window]  # una cada 1/rate
    if pattern == "pico":
        return _peak(rng, rate, window, peak_factor)
    if pattern == "rafaga":
        return _bursts(rng, rate, window, burst_factor, burst_fraction, burst_len)
    raise ValueError(f"patrón de llegadas desconocido: {pattern}")
