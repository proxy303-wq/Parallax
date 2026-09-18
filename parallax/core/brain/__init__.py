"""The PARALLAX brain — DeepSeek reasoning + multi-validator + persistent memory.

DeepSeek is the *advisory reasoning brain* (synthesis, counter-arguments,
natural-language explanation).  The type-safe engine remains the sole authority
for prices, positions, risk limits and executable order parameters.  The brain
may only ever BLOCK or REDUCE a trade — never create, size or send one.
"""
from .assessment import BrainAssessment
from .brain import Brain
from .llm import DeepSeekClient, OpenAICompatClient
from .typesafe import TypeSafeSystemOne
from .validators import ValidatorEnsemble

__all__ = ["Brain", "BrainAssessment", "DeepSeekClient", "OpenAICompatClient",
           "TypeSafeSystemOne", "ValidatorEnsemble"]
