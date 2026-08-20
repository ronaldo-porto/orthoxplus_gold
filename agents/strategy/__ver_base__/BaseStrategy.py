# SPDX-License-Identifier: MIT
"""
BaseStrategy — deploy structural baseline for SN79.

Public deploy entry point.  The actual implementation is generated from the exact
local Strategy1 Research V4.1 Strict lineage into ``_BaseStrategy_flat.py`` and
then imported here.

The generated class has a simple MRO:

    BaseStrategy -> <TAOS framework base>

It does NOT inherit Strategy1, Strategy1_Debug, or DetailedTemplateAgent.

Why generate instead of manually copying?
-----------------------------------------
V4.1's validated behavior is distributed across the research class and its
historical parent layers. Flattening the exact local sources prevents a deploy
rewrite from silently changing signal, PnL, inventory, quote, or scheduler
semantics.

The run script rebuilds the flat implementation only when a source hash changes.
Detailed V4.1 telemetry is OFF by default and can be enabled with:

    bash run_base_strategy.sh --log
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_FLAT = _HERE / "_BaseStrategy_flat.py"
_BUILDER = _HERE / "build_base_strategy.py"


def _ensure_flat() -> None:
    if not _BUILDER.is_file():
        raise ImportError(
            f"BaseStrategy builder is missing: {_BUILDER}. "
            "Copy build_base_strategy.py beside BaseStrategy.py."
        )

    # Load the builder by file path so importing BaseStrategy does not depend on
    # the current working directory or package layout.
    spec = importlib.util.spec_from_file_location("_base_strategy_builder", _BUILDER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load BaseStrategy builder: {_BUILDER}")
    builder = importlib.util.module_from_spec(spec)
    # dataclasses/type-resolution inside the builder expect its module to be
    # present in sys.modules while exec_module() runs.
    import sys
    sys.modules[spec.name] = builder
    spec.loader.exec_module(builder)

    current = builder.output_is_current(_HERE, _FLAT)
    if not current:
        # Build occurs only at process import/startup, never on the trading hot path.
        builder.write_flat(_HERE, _FLAT)


def _load_flat():
    _ensure_flat()
    spec = importlib.util.spec_from_file_location("_base_strategy_flat", _FLAT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load generated BaseStrategy implementation: {_FLAT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_impl = _load_flat()
BaseStrategy = _impl.BaseStrategy
BASESTRATEGY_BUILD_META = _impl.BASESTRATEGY_BUILD_META

__all__ = ["BaseStrategy", "BASESTRATEGY_BUILD_META"]


if __name__ == "__main__":
    from taos.common.agents import launch
    launch(BaseStrategy)
