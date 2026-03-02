#!/usr/bin/env python3
"""
AvaAI — FundManagerAI companion app.
DeFi yield dashboard, deposit/withdraw/claim helpers, and stats. Single-file 1700+ LOC.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# AvaAI config (unique names; do not reuse from other codebases)
# -----------------------------------------------------------------------------
AVAAI_APP_NAME = "AvaAI"
AVAAI_VERSION = "1.0.0"
AVAAI_DEFAULT_RPC = "https://eth.llamarpc.com"
AVAAI_CONFIG_DIR = os.environ.get("AVAAI_CONFIG_DIR", os.path.expanduser("~/.avaai"))
AVAAI_CONTRACT_ADDRESS_ENV = "AVAAI_FUND_MANAGER_AI_ADDRESS"
AVAAI_RPC_ENV = "AVAAI_RPC_URL"
AVAAI_PRIVATE_KEY_ENV = "AVAAI_PRIVATE_KEY"
AVAAI_CHAIN_ID_MAINNET = 1
AVAAI_CHAIN_ID_SEPOLIA = 11155111
AVAAI_BPS = 10000
AVAAI_DECIMALS = 18
AVAAI_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
AVAAI_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Minimal ABI for FundManagerAI (FMAI) view + write
FMAI_ABI = [
    {"inputs": [], "name": "getGlobalStats", "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "user", "type": "address"}, {"name": "token", "type": "address"}], "name": "getDepositBalance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "user", "type": "address"}, {"name": "token", "type": "address"}], "name": "getClaimableYield", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "token", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "deposit", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "token", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "withdraw", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "token", "type": "address"}], "name": "claimYield", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "getTokenList", "outputs": [{"name": "", "type": "address[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "strategyId", "type": "uint256"}], "name": "getStrategy", "outputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "bool"}, {"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "strategyCount", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getFeeConfig", "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getConstantsBundle", "outputs": [{"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}, {"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------
@dataclass
class AvaAIConfig:
    rpc_url: str = AVAAI_DEFAULT_RPC
    contract_address: Optional[str] = None
    chain_id: int = AVAAI_CHAIN_ID_MAINNET
    private_key: Optional[str] = None
    gas_limit_default: int = 300_000
    gas_price_gwei: Optional[float] = None


@dataclass
class GlobalStats:
    total_deposited: int
    total_withdrawn: int
    total_yield_harvested: int
    strategy_count: int
    paused: bool


@dataclass
class StrategyInfo:
    strategy_id: int
    target: str
    token: str
    allocated: int
    harvested: int
    cap_bps: int
    active: bool
    added_at_block: int


@dataclass
class UserPosition:
    token: str
    deposit_balance: int
    claimable_yield: int


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format=AVAAI_LOG_FMT, datefmt=AVAAI_DATE_FMT)
    log = logging.getLogger(AVAAI_APP_NAME)
    return log


# -----------------------------------------------------------------------------
# Config load/save
# -----------------------------------------------------------------------------
def load_config(path: Optional[str] = None) -> AvaAIConfig:
    p = path or os.path.join(AVAAI_CONFIG_DIR, "config.json")
