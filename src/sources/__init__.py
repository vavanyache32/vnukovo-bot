"""Source adapters. Every adapter returns either:
   * raw text (for METAR sources) → parsed by parser.metar
   * a structured dict / list (Synoptic, Open-Meteo, Polymarket).

All adapters use the shared HTTP client and respect per-host proxy mounts.
"""
