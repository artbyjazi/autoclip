"""Crop-path smoothing.

Raw per-frame face positions are far too noisy to drive a crop directly —
detection jitter of a few pixels reads as a shaking camera. The chain here is
deliberately three stages:

1. **One Euro Filter** — adaptive low-pass. At low speed it filters hard (kills
   jitter); at high speed it filters lightly (keeps up with a real pan). A plain
   EMA has to choose one or the other, so it either shakes or lags.
2. **Dead zone** — below a movement threshold, don't move at all. A locked frame
   reads as intentional; a frame that micro-corrects reads as broken.
3. **Velocity clamp** — cap how fast the crop can travel, so a detection glitch
   can never whip the frame across the shot.

Reference: Casiez, Roussel & Vogel, "1€ Filter" (CHI 2012).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Lower cutoff filters harder at rest. Tuned for face tracking at 5-10 Hz
#: sampling, where detection noise dominates below ~1 Hz.
DEFAULT_MIN_CUTOFF = 0.6
#: How aggressively the cutoff opens up with speed. Higher means less lag on
#: fast movement, at the cost of letting more jitter through.
DEFAULT_BETA = 0.02
DEFAULT_DERIVATIVE_CUTOFF = 1.0


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class LowPass:
    """First-order low-pass filter with a settable per-sample alpha."""

    def __init__(self) -> None:
        self.value: float | None = None

    def __call__(self, sample: float, alpha: float) -> float:
        if self.value is None:
            self.value = sample
        else:
            self.value = alpha * sample + (1.0 - alpha) * self.value
        return self.value


class OneEuroFilter:
    """Adaptive low-pass filter trading jitter against lag by signal speed."""

    def __init__(
        self,
        *,
        min_cutoff: float = DEFAULT_MIN_CUTOFF,
        beta: float = DEFAULT_BETA,
        derivative_cutoff: float = DEFAULT_DERIVATIVE_CUTOFF,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.derivative_cutoff = derivative_cutoff
        self._value = LowPass()
        self._derivative = LowPass()
        self._last_sample: float | None = None
        self._last_time: float | None = None

    def __call__(self, sample: float, timestamp: float) -> float:
        if self._last_time is None or timestamp <= self._last_time:
            self._last_time = timestamp
            self._last_sample = sample
            return self._value(sample, 1.0)

        dt = timestamp - self._last_time
        derivative = (sample - (self._last_sample or sample)) / dt
        smoothed_derivative = self._derivative(derivative, _alpha(self.derivative_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(smoothed_derivative)
        result = self._value(sample, _alpha(cutoff, dt))

        self._last_time = timestamp
        self._last_sample = sample
        return result


@dataclass
class SmoothingConfig:
    min_cutoff: float = DEFAULT_MIN_CUTOFF
    beta: float = DEFAULT_BETA
    #: Movement smaller than this (pixels) is ignored entirely.
    dead_zone_px: float = 12.0
    #: Ceiling on crop travel, in pixels per second.
    max_velocity_px_s: float = 260.0


def smooth_series(
    samples: list[tuple[float, float]], config: SmoothingConfig | None = None
) -> list[tuple[float, float]]:
    """Smooth ``(timestamp, value)`` samples through the full three-stage chain.

    Preconditions:
        samples are sorted by timestamp.
    """
    config = config or SmoothingConfig()
    if len(samples) <= 1:
        return list(samples)

    one_euro = OneEuroFilter(min_cutoff=config.min_cutoff, beta=config.beta)

    output: list[tuple[float, float]] = []
    held: float | None = None
    previous_time: float | None = None

    for timestamp, raw in samples:
        filtered = one_euro(raw, timestamp)

        if held is None:
            held = filtered
        else:
            # Dead zone: hold position until the target has moved meaningfully.
            if abs(filtered - held) >= config.dead_zone_px:
                target = filtered
                if previous_time is not None:
                    dt = max(1e-6, timestamp - previous_time)
                    max_step = config.max_velocity_px_s * dt
                    delta = target - held
                    if abs(delta) > max_step:
                        target = held + math.copysign(max_step, delta)
                held = target

        output.append((timestamp, held))
        previous_time = timestamp

    return output
