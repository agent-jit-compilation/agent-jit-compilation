# Modified from WebArena (https://github.com/web-arena-x/webarena) at commit dce0468
# by the Agent JIT Compilation authors. Licensed under the Apache License, Version 2.0.
"""This module is adapt from https://github.com/zeno-ml/zeno-build"""

# HuggingFace support is optional (requires text_generation package)
try:
    from .providers.hf_utils import generate_from_huggingface_completion
except ImportError:
    generate_from_huggingface_completion = None  # type: ignore

from .providers.openai_utils import (
    generate_from_openai_chat_completion,
    generate_from_openai_completion,
)
from .utils import call_llm

__all__ = [
    "generate_from_openai_completion",
    "generate_from_openai_chat_completion",
    "generate_from_huggingface_completion",
    "call_llm",
]
