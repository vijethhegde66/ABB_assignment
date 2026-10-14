"""Alarm Management API simulator.

Implements the contract defined by the Postman collections shipped with the
assignment (``postman/``). Data is synthetic but deterministic: the same seed
and time window always produce the same assets, alarms and analytics, and the
generator embeds realistic causal patterns (deaerator level -> pump suction
pressure, lube oil pressure -> bearing temperature, ...) so that correlation,
priority scoring and recommendations return meaningful results.
"""

__version__ = "1.0.0"
