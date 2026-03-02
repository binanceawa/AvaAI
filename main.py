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
    cfg = AvaAIConfig()
    cfg.contract_address = os.environ.get(AVAAI_CONTRACT_ADDRESS_ENV)
    cfg.rpc_url = os.environ.get(AVAAI_RPC_ENV) or cfg.rpc_url
    cfg.private_key = os.environ.get(AVAAI_PRIVATE_KEY_ENV)
    if os.path.isfile(p):
        try:
            with open(p, "r") as f:
                data = json.load(f)
            cfg.rpc_url = data.get("rpc_url", cfg.rpc_url)
            cfg.contract_address = data.get("contract_address") or cfg.contract_address
            cfg.chain_id = data.get("chain_id", cfg.chain_id)
            if data.get("private_key"):
                cfg.private_key = data["private_key"]
            cfg.gas_limit_default = data.get("gas_limit_default", cfg.gas_limit_default)
            cfg.gas_price_gwei = data.get("gas_price_gwei")
        except Exception:
            pass
    return cfg


def save_config(cfg: AvaAIConfig, path: Optional[str] = None) -> None:
    p = path or os.path.join(AVAAI_CONFIG_DIR, "config.json")
    Path(AVAAI_CONFIG_DIR).mkdir(parents=True, exist_ok=True)
    data = {
        "rpc_url": cfg.rpc_url,
        "contract_address": cfg.contract_address,
        "chain_id": cfg.chain_id,
        "gas_limit_default": cfg.gas_limit_default,
        "gas_price_gwei": cfg.gas_price_gwei,
    }
    if cfg.private_key:
        data["private_key"] = cfg.private_key
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


# -----------------------------------------------------------------------------
# Web3 helpers (optional dependency)
# -----------------------------------------------------------------------------
def _try_import_web3() -> Any:
    try:
        from web3 import Web3
        return Web3
    except ImportError:
        return None


def wei_to_human(wei: int, decimals: int = AVAAI_DECIMALS) -> str:
    d = Decimal(wei) / Decimal(10 ** decimals)
    return str(d.quantize(Decimal(10) ** -min(decimals, 8)))


def human_to_wei(amount: str, decimals: int = AVAAI_DECIMALS) -> int:
    d = Decimal(amount) * Decimal(10 ** decimals)
    return int(d)


def bps_to_percent(bps: int) -> str:
    return f"{(bps / AVAAI_BPS) * 100:.2f}%"


# -----------------------------------------------------------------------------
# Contract interface (read-only when no web3)
# -----------------------------------------------------------------------------
class FundManagerAIClient:
    def __init__(self, config: AvaAIConfig, log: Optional[logging.Logger] = None):
        self.config = config
        self.log = log or logging.getLogger(AVAAI_APP_NAME)
        self._w3 = None
        self._contract = None
        self._account = None
        if config.contract_address and config.rpc_url:
            self._init_web3()

    def _init_web3(self) -> None:
        Web3 = _try_import_web3()
        if not Web3:
            self.log.warning("web3 not installed; run: pip install web3")
            return
        self._w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))
        if not self._w3.is_connected():
            self.log.warning("RPC not connected: %s", self.config.rpc_url)
            return
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(self.config.contract_address),
            abi=FMAI_ABI,
        )
        if self.config.private_key:
            try:
                from eth_account import Account
                self._account = Account.from_key(self.config.private_key)
            except Exception as e:
                self.log.warning("Could not load account: %s", e)

    @property
    def is_ready(self) -> bool:
        return self._contract is not None and (self._w3 is not None and self._w3.is_connected())

    def get_global_stats(self) -> Optional[GlobalStats]:
        if not self.is_ready:
            return None
        try:
            r = self._contract.functions.getGlobalStats().call()
            return GlobalStats(
                total_deposited=r[0],
                total_withdrawn=r[1],
                total_yield_harvested=r[2],
                strategy_count=r[3],
                paused=r[4],
            )
        except Exception as e:
            self.log.error("getGlobalStats failed: %s", e)
            return None

    def get_deposit_balance(self, user: str, token: str) -> Optional[int]:
        if not self.is_ready:
            return None
        try:
            return self._contract.functions.getDepositBalance(
                self._w3.to_checksum_address(user),
                self._w3.to_checksum_address(token),
            ).call()
        except Exception as e:
            self.log.error("getDepositBalance failed: %s", e)
            return None

    def get_claimable_yield(self, user: str, token: str) -> Optional[int]:
        if not self.is_ready:
            return None
        try:
            return self._contract.functions.getClaimableYield(
                self._w3.to_checksum_address(user),
                self._w3.to_checksum_address(token),
            ).call()
        except Exception as e:
            self.log.error("getClaimableYield failed: %s", e)
            return None

    def get_token_list(self) -> List[str]:
        if not self.is_ready:
            return []
        try:
            addrs = self._contract.functions.getTokenList().call()
            return [a for a in addrs if a]
        except Exception as e:
            self.log.error("getTokenList failed: %s", e)
            return []

    def get_strategy(self, strategy_id: int) -> Optional[StrategyInfo]:
        if not self.is_ready:
            return None
        try:
            r = self._contract.functions.getStrategy(strategy_id).call()
            return StrategyInfo(
                strategy_id=strategy_id,
                target=r[0],
                token=r[1],
                allocated=r[2],
                harvested=r[3],
                cap_bps=r[4],
                active=r[5],
                added_at_block=r[6],
            )
        except Exception as e:
            self.log.error("getStrategy(%s) failed: %s", strategy_id, e)
            return None

    def get_strategy_count(self) -> int:
        if not self.is_ready:
            return 0
        try:
            return self._contract.functions.strategyCount().call()
        except Exception:
            return 0

    def get_fee_config(self) -> Tuple[int, int]:
        if not self.is_ready:
            return 0, 0
        try:
            r = self._contract.functions.getFeeConfig().call()
            return (r[0], r[1])
        except Exception:
            return 0, 0

    def get_constants_bundle(self) -> Optional[Dict[str, Any]]:
        if not self.is_ready:
            return None
        try:
            r = self._contract.functions.getConstantsBundle().call()
            return {
                "bps": r[0],
                "max_fee_bps": r[1],
                "min_deposit": r[2],
                "max_strategies": r[3],
                "harvest_cooldown_blocks": r[4],
                "vesting_blocks": r[5],
                "strategy_cap_bps": r[6],
            }
        except Exception:
            return None

    def deposit(self, token: str, amount_wei: int) -> Optional[str]:
        if not self.is_ready or not self._account:
            self.log.error("Not ready or no account for deposit")
            return None
        try:
            tx = self._contract.functions.deposit(
                self._w3.to_checksum_address(token),
                amount_wei,
            ).build_transaction({
                "from": self._account.address,
                "gas": self.config.gas_limit_default,
            })
            if self.config.gas_price_gwei:
