# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Miner-style dynamic load of standalone BaseStrategy.py.

taos.common.neurons.miner instantiates agents with:

    module_spec = importlib.util.spec_from_file_location(
        name, os.path.join(path, name + '.py'))
    agent_module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = agent_module
    module_spec.loader.exec_module(agent_module)

That path does not put agents/strategy on sys.path. This test copies only
BaseStrategy.py into an empty directory so sibling helper modules cannot
rescue a leftover import.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_SRC = ROOT / "agents" / "strategy" / "BaseStrategy.py"

SIBLING_HELPERS = (
    "regime_v2",
    "execution_lifecycle",
    "execution_hazard",
    "score_ev",
    "quote_hysteresis",
    "adverse",
    "entry_size",
    "realization",
    "candidate_screen",
    "research_regime_v2",
    "research_fill_hazard",
    "research_score_ev",
    "research_quote_hysteresis",
    "research_quote_lifecycle",
    "research_candidate_screen",
    "research_adverse",
    "research_entry_size",
    "research_realization",
    "Strategy1_Research",
    "AdaptiveAgent",
)


class _Dummy:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _Dummy()


def _install_runtime_stubs(modules: dict[str, types.ModuleType]) -> None:
    """Stub bittensor / taos so exec_module can finish without the live stack."""

    def pkg(name: str) -> types.ModuleType:
        if name in modules:
            return modules[name]
        mod = types.ModuleType(name)
        mod.__path__ = []  # type: ignore[attr-defined]
        modules[name] = mod
        return mod

    def leaf(name: str) -> types.ModuleType:
        if name in modules:
            return modules[name]
        mod = types.ModuleType(name)
        modules[name] = mod
        return mod

    bt = leaf("bittensor")
    bt.logging = types.SimpleNamespace(  # type: ignore[attr-defined]
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        success=lambda *a, **k: None,
        trace=lambda *a, **k: None,
    )

    taos = pkg("taos")
    common = pkg("taos.common")
    agents = leaf("taos.common.agents")
    agents.launch = lambda *a, **k: None  # type: ignore[attr-defined]
    taos.common = common
    common.agents = agents

    im = pkg("taos.im")
    im_agents = leaf("taos.im.agents")
    im_agents.FinanceSimulationAgent = _Dummy  # type: ignore[attr-defined]
    taos.im = im
    im.agents = im_agents

    protocol = pkg("taos.im.protocol")
    protocol.FinanceAgentResponse = _Dummy
    protocol.MarketSimulationStateUpdate = _Dummy
    im.protocol = protocol

    events = leaf("taos.im.protocol.events")
    events.SimulationStartEvent = _Dummy
    events.TradeEvent = _Dummy
    protocol.events = events

    instructions = leaf("taos.im.protocol.instructions")
    instructions.CancelOrdersInstruction = _Dummy
    instructions.ClosePositionsInstruction = _Dummy
    instructions.PlaceLimitOrderInstruction = _Dummy
    instructions.PlaceMarketOrderInstruction = _Dummy
    protocol.instructions = instructions

    models = leaf("taos.im.protocol.models")
    models.Account = _Dummy
    models.Book = _Dummy
    models.LoanSettlementOption = _Dummy
    models.OrderCurrency = _Dummy
    models.OrderDirection = _Dummy
    models.STP = _Dummy
    models.TimeInForce = _Dummy
    protocol.models = models

    utils = pkg("taos.im.utils")
    utils.duration_from_timestamp = lambda *a, **k: 0  # type: ignore[attr-defined]
    kappa = leaf("taos.im.utils.kappa")
    kappa.kappa_3 = lambda *a, **k: 0.0  # type: ignore[attr-defined]
    im.utils = utils
    utils.kappa = kappa


def _miner_exec_module(path: str, name: str):
    """Match taos.common.neurons.miner agent loading."""
    module_spec = importlib.util.spec_from_file_location(
        name, os.path.join(path, name + ".py")
    )
    assert module_spec is not None
    assert module_spec.loader is not None
    agent_module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = agent_module
    module_spec.loader.exec_module(agent_module)
    return agent_module


def test_base_strategy_dynamic_import_without_sibling_helpers():
    src = BASE_SRC.read_text(encoding="utf-8")
    assert "from regime_v2 import" not in src
    assert "from execution_lifecycle import" not in src
    assert "from execution_hazard import" not in src
    assert "from score_ev import" not in src
    assert "from quote_hysteresis import" not in src

    saved = {k: sys.modules.get(k) for k in list(sys.modules)}
    tmp = tempfile.mkdtemp(prefix="base_dynimport_")
    try:
        shutil.copy2(BASE_SRC, os.path.join(tmp, "BaseStrategy.py"))
        for helper in SIBLING_HELPERS:
            assert not os.path.exists(os.path.join(tmp, helper + ".py"))

        stubs: dict[str, types.ModuleType] = {}
        _install_runtime_stubs(stubs)
        for name, mod in stubs.items():
            sys.modules[name] = mod
        for helper in SIBLING_HELPERS:
            sys.modules.pop(helper, None)

        agent_module = _miner_exec_module(tmp, "BaseStrategy")
        agent_class = getattr(agent_module, "BaseStrategy")

        assert callable(agent_module.classify_regime_v2)
        assert agent_module.DebounceState is not None
        assert agent_module.RegimeV2Thresholds is not None
        assert agent_module.QuoteLifecycleStore is not None
        assert agent_module.QuoteRecord is not None
        assert callable(agent_module.classify_fill)
        assert callable(agent_module.sim_delta_ms)
        assert callable(agent_module.ms_to_ns)
        assert agent_module.FillHazardModel is not None
        assert agent_module.HazardFeatures is not None
        assert agent_module.HazardPrediction is not None
        assert callable(agent_module.compute_score_ev)
        assert callable(agent_module.required_observation_count)
        assert callable(agent_module.select_rank)
        assert callable(agent_module.choose_ttl_ms)
        assert callable(agent_module.predicted_dust_blocks_increase)
        assert callable(agent_module.should_replace_quote)
        assert callable(agent_module.would_create_dust)
        assert agent_class.__name__ == "BaseStrategy"
        assert getattr(agent_class, "DEPLOY_POLICY_VERSION") == "base_v4_4_champion"
        assert getattr(agent_class, "BASE_CHAMPION") is True
        assert getattr(agent_class, "BASE_CHAMPION_FROZEN") is True
        assert not hasattr(agent_module, "dust_escape_allowed")

        for helper in SIBLING_HELPERS:
            assert helper not in sys.modules
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for name in list(sys.modules):
            if name not in saved:
                sys.modules.pop(name, None)
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
