"""Vendored Smart Money Concepts indicator (MIT).

Source : https://github.com/joshyattridge/smart-money-concepts
File   : smartmoneyconcepts/smc.py, taken at the commit the crypto walk-forward
         was validated against.  Vendored rather than installed because:

  * the PyPI build (0.0.27) requires numba and prints a Unicode banner on import
    that raises UnicodeEncodeError on a cp1252 Windows console;
  * live signals must match the backtested signals exactly, so the code is
    pinned here rather than resolved from an index.

License: MIT (see LICENSE.smart-money-concepts).  Original copyright
(c) 2020 NeuralNine / Josh Attridge.  Unmodified apart from this wrapper.
"""
# NOTE: smartmoneyconcepts/smc.py defines a *class* named 'smc' whose methods are
# classmethods (wrapped by its @apply decorator).  Import the class, not the module --
# 'from parallax.adapters.smc import smc' would bind the module and every call would
# fail with AttributeError.
from parallax.adapters.smc.smc import smc  # noqa: F401

__all__ = ["smc"]
