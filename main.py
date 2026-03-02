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
                tx["gasPrice"] = self._w3.to_wei(self.config.gas_price_gwei, "gwei")
            signed = self._w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            return self._w3.to_hex(tx_hash)
        except Exception as e:
            self.log.error("deposit failed: %s", e)
            return None

    def withdraw(self, token: str, amount_wei: int) -> Optional[str]:
        if not self.is_ready or not self._account:
            self.log.error("Not ready or no account for withdraw")
            return None
        try:
            tx = self._contract.functions.withdraw(
                self._w3.to_checksum_address(token),
                amount_wei,
            ).build_transaction({
                "from": self._account.address,
                "gas": self.config.gas_limit_default,
            })
            if self.config.gas_price_gwei:
                tx["gasPrice"] = self._w3.to_wei(self.config.gas_price_gwei, "gwei")
            signed = self._w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            return self._w3.to_hex(tx_hash)
        except Exception as e:
            self.log.error("withdraw failed: %s", e)
            return None

    def claim_yield(self, token: str) -> Optional[str]:
        if not self.is_ready or not self._account:
            self.log.error("Not ready or no account for claimYield")
            return None
        try:
            tx = self._contract.functions.claimYield(
                self._w3.to_checksum_address(token),
            ).build_transaction({
                "from": self._account.address,
                "gas": self.config.gas_limit_default,
            })
            if self.config.gas_price_gwei:
                tx["gasPrice"] = self._w3.to_wei(self.config.gas_price_gwei, "gwei")
            signed = self._w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            return self._w3.to_hex(tx_hash)
        except Exception as e:
            self.log.error("claimYield failed: %s", e)
            return None


# -----------------------------------------------------------------------------
# CLI: stats
# -----------------------------------------------------------------------------
def cmd_stats(client: FundManagerAIClient, _args: argparse.Namespace, log: logging.Logger) -> int:
    stats = client.get_global_stats()
    if not stats:
        log.error("Could not fetch global stats (check RPC and contract address)")
        return 1
    log.info("=== FundManagerAI global stats ===")
    log.info("Total deposited:  %s wei", stats.total_deposited)
    log.info("Total withdrawn:  %s wei", stats.total_withdrawn)
    log.info("Total yield:      %s wei", stats.total_yield_harvested)
    log.info("Strategies:       %s", stats.strategy_count)
    log.info("Paused:           %s", stats.paused)
    perf_bps, dep_bps = client.get_fee_config()
    log.info("Performance fee:  %s (%s bps)", bps_to_percent(perf_bps), perf_bps)
    log.info("Deposit fee:      %s (%s bps)", bps_to_percent(dep_bps), dep_bps)
    return 0


# -----------------------------------------------------------------------------
# CLI: strategies
# -----------------------------------------------------------------------------
def cmd_strategies(client: FundManagerAIClient, _args: argparse.Namespace, log: logging.Logger) -> int:
    n = client.get_strategy_count()
    if n == 0:
        log.info("No strategies")
        return 0
    log.info("=== Strategies (count=%s) ===", n)
    for i in range(1, n + 1):
        s = client.get_strategy(i)
        if not s:
            continue
        log.info("  [%s] target=%s token=%s allocated=%s harvested=%s cap_bps=%s active=%s",
                 s.strategy_id, s.target[:10] + "..", s.token[:10] + "..",
                 s.allocated, s.harvested, s.cap_bps, s.active)
    return 0


# -----------------------------------------------------------------------------
# CLI: tokens
# -----------------------------------------------------------------------------
def cmd_tokens(client: FundManagerAIClient, _args: argparse.Namespace, log: logging.Logger) -> int:
    tokens = client.get_token_list()
    if not tokens:
        log.info("No tokens in list (or RPC/contract not ready)")
        return 0
    log.info("=== Allowed tokens (%s) ===", len(tokens))
    for t in tokens:
        log.info("  %s", t)
    return 0


# -----------------------------------------------------------------------------
# CLI: position
# -----------------------------------------------------------------------------
def cmd_position(client: FundManagerAIClient, args: argparse.Namespace, log: logging.Logger) -> int:
    user = args.user or (client._account.address if client._account else None)
    if not user:
        log.error("Provide --user or set private key")
        return 1
    tokens = client.get_token_list()
    if not tokens:
        tokens = [args.token] if args.token else []
    if not tokens:
        log.error("No tokens; use --token ADDRESS or ensure contract has token list")
        return 1
    log.info("=== Position for %s ===", user[:12] + "..")
    for token in tokens:
        bal = client.get_deposit_balance(user, token)
        claim = client.get_claimable_yield(user, token)
        if bal is None and claim is None:
            continue
        log.info("  token %s: balance=%s wei, claimable_yield=%s wei",
                 token[:12] + "..", bal or 0, claim or 0)
        if args.human:
            log.info("    -> balance=%s, claimable=%s", wei_to_human(bal or 0), wei_to_human(claim or 0))
    return 0


# -----------------------------------------------------------------------------
# CLI: deposit / withdraw / claim
# -----------------------------------------------------------------------------
def cmd_deposit(client: FundManagerAIClient, args: argparse.Namespace, log: logging.Logger) -> int:
    amount_wei = human_to_wei(args.amount) if args.human else int(args.amount)
    tx = client.deposit(args.token, amount_wei)
    if not tx:
        return 1
    log.info("Deposit tx: %s", tx)
    return 0


def cmd_withdraw(client: FundManagerAIClient, args: argparse.Namespace, log: logging.Logger) -> int:
    amount_wei = human_to_wei(args.amount) if args.human else int(args.amount)
    tx = client.withdraw(args.token, amount_wei)
    if not tx:
        return 1
    log.info("Withdraw tx: %s", tx)
    return 0


def cmd_claim(client: FundManagerAIClient, args: argparse.Namespace, log: logging.Logger) -> int:
    tx = client.claim_yield(args.token)
    if not tx:
        return 1
    log.info("Claim yield tx: %s", tx)
    return 0


# -----------------------------------------------------------------------------
# CLI: config
# -----------------------------------------------------------------------------
def cmd_config_get(client: FundManagerAIClient, _args: argparse.Namespace, log: logging.Logger) -> int:
    c = client.config
    log.info("rpc_url=%s", c.rpc_url)
    log.info("contract_address=%s", c.contract_address or "(not set)")
    log.info("chain_id=%s", c.chain_id)
    log.info("private_key=%s", "***" if c.private_key else "(not set)")
    return 0


def cmd_config_set(args: argparse.Namespace, log: logging.Logger) -> int:
    cfg = load_config(args.config)
    if args.rpc:
        cfg.rpc_url = args.rpc
    if args.contract:
        cfg.contract_address = args.contract
    if args.chain_id is not None:
        cfg.chain_id = args.chain_id
    save_config(cfg, args.config)
    log.info("Config saved")
    return 0


# -----------------------------------------------------------------------------
# Yield math and simulation (AvaAI-specific)
# -----------------------------------------------------------------------------
def avaai_fee_from_bps(amount_wei: int, bps: int) -> int:
    return (amount_wei * bps) // AVAAI_BPS


def avaai_net_after_deposit_fee(amount_wei: int, dep_bps: int) -> int:
    return amount_wei - avaai_fee_from_bps(amount_wei, dep_bps)


def avaai_net_after_perf_fee(amount_wei: int, perf_bps: int) -> int:
    return amount_wei - avaai_fee_from_bps(amount_wei, perf_bps)


def avaai_vested_amount(vesting_amount: int, start_block: int, end_block: int, current_block: int) -> int:
    if current_block >= end_block:
        return vesting_amount
    if current_block <= start_block:
        return 0
    elapsed = current_block - start_block
    duration = end_block - start_block
    if duration == 0:
        return 0
    return (vesting_amount * elapsed) // duration


def avaai_estimate_apy_from_harvest(harvested_wei: int, allocated_wei: int, blocks_per_year: int = 2_628_000) -> str:
    if allocated_wei == 0:
        return "0%"
    ratio = Decimal(harvested_wei) / Decimal(allocated_wei)
    apy = ratio * Decimal(blocks_per_year)
    return f"{float(apy) * 100:.2f}%"


def avaai_format_wei(wei: int, decimals: int = AVAAI_DECIMALS) -> str:
    return wei_to_human(wei, decimals)


def avaai_short_address(addr: str, prefix: int = 6, suffix: int = 4) -> str:
    if not addr or len(addr) < prefix + suffix:
        return addr or ""
    if addr.startswith("0x"):
        return f"0x{addr[2:2+prefix]}...{addr[-suffix:]}"
    return f"{addr[:prefix]}...{addr[-suffix:]}"


def avaai_validate_address(addr: str) -> bool:
    if not addr or not isinstance(addr, str):
        return False
    addr = addr.strip()
    if not addr.startswith("0x"):
        return False
    rest = addr[2:].lower()
    if len(rest) != 40:
        return False
    return all(c in "0123456789abcdef" for c in rest)


def avaai_parse_amount(s: str, decimals: int = AVAAI_DECIMALS) -> Optional[int]:
    try:
        return human_to_wei(s.strip(), decimals)
    except Exception:
        return None


def avaai_report_global(stats: GlobalStats, perf_bps: int, dep_bps: int) -> List[str]:
    lines = [
        "=== FundManagerAI Global Report ===",
        f"Total deposited (wei):    {stats.total_deposited}",
        f"Total withdrawn (wei):    {stats.total_withdrawn}",
        f"Total yield harvested:    {stats.total_yield_harvested}",
        f"Strategy count:           {stats.strategy_count}",
        f"Paused:                   {stats.paused}",
        f"Performance fee (bps):    {perf_bps} -> {bps_to_percent(perf_bps)}",
        f"Deposit fee (bps):        {dep_bps} -> {bps_to_percent(dep_bps)}",
    ]
    return lines


def avaai_report_strategy(s: StrategyInfo) -> List[str]:
    return [
        f"Strategy #{s.strategy_id}",
        f"  target:       {avaai_short_address(s.target)}",
        f"  token:        {avaai_short_address(s.token)}",
        f"  allocated:    {s.allocated} wei",
        f"  harvested:    {s.harvested} wei",
        f"  cap_bps:      {s.cap_bps}",
        f"  active:       {s.active}",
        f"  added_block:  {s.added_at_block}",
    ]


def avaai_report_position(user: str, positions: List[UserPosition]) -> List[str]:
    lines = [f"=== Position for {avaai_short_address(user)} ==="]
    for p in positions:
        lines.append(f"  token {avaai_short_address(p.token)}: balance={p.deposit_balance} wei, claimable={p.claimable_yield} wei")
    return lines


# -----------------------------------------------------------------------------
# Simulated backend (no RPC)
# -----------------------------------------------------------------------------
class AvaAISimulator:
    def __init__(self):
        self._total_deposited = 0
        self._total_withdrawn = 0
        self._total_yield = 0
        self._strategies: Dict[int, Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._strategy_counter = 0

    def add_strategy(self, target: str, token: str, cap_bps: int = 5000) -> int:
        self._strategy_counter += 1
        self._strategies[self._strategy_counter] = {
            "target": target,
            "token": token,
            "allocated": 0,
            "harvested": 0,
            "cap_bps": cap_bps,
            "active": True,
        }
        return self._strategy_counter

    def deposit_sim(self, user: str, token: str, amount_wei: int, fee_bps: int = 10) -> int:
        fee = avaai_fee_from_bps(amount_wei, fee_bps)
        net = amount_wei - fee
        key = (user.lower(), token.lower())
        if user not in self._positions:
            self._positions[user] = {}
        if token not in self._positions[user]:
            self._positions[user][token] = {"deposited": 0, "withdrawn": 0, "claimable_yield": 0}
        self._positions[user][token]["deposited"] += net
        self._total_deposited += amount_wei
        return net

    def withdraw_sim(self, user: str, token: str, amount_wei: int) -> bool:
        key = (user.lower(), token.lower())
        if user not in self._positions or token not in self._positions[user]:
            return False
        bal = self._positions[user][token]["deposited"] - self._positions[user][token]["withdrawn"]
        if amount_wei > bal:
            return False
        self._positions[user][token]["withdrawn"] += amount_wei
        self._total_withdrawn += amount_wei
        return True

    def credit_yield_sim(self, user: str, token: str, amount_wei: int) -> None:
        if user not in self._positions:
            self._positions[user] = {}
        if token not in self._positions[user]:
            self._positions[user][token] = {"deposited": 0, "withdrawn": 0, "claimable_yield": 0}
        self._positions[user][token]["claimable_yield"] += amount_wei
        self._total_yield += amount_wei

    def get_stats_sim(self) -> GlobalStats:
        return GlobalStats(
            total_deposited=self._total_deposited,
            total_withdrawn=self._total_withdrawn,
            total_yield_harvested=self._total_yield,
            strategy_count=len(self._strategies),
            paused=False,
        )

    def get_balance_sim(self, user: str, token: str) -> int:
        if user not in self._positions or token not in self._positions[user]:
            return 0
        d = self._positions[user][token]
        return d["deposited"] - d["withdrawn"]

    def get_claimable_sim(self, user: str, token: str) -> int:
        if user not in self._positions or token not in self._positions[user]:
            return 0
        return self._positions[user][token].get("claimable_yield", 0)


# -----------------------------------------------------------------------------
# Batch and export helpers
# -----------------------------------------------------------------------------
def avaai_export_stats_json(stats: GlobalStats, fee_config: Tuple[int, int], constants: Optional[Dict]) -> str:
    d: Dict[str, Any] = {
        "total_deposited": stats.total_deposited,
        "total_withdrawn": stats.total_withdrawn,
        "total_yield_harvested": stats.total_yield_harvested,
        "strategy_count": stats.strategy_count,
        "paused": stats.paused,
        "performance_fee_bps": fee_config[0],
        "deposit_fee_bps": fee_config[1],
    }
    if constants:
        d["constants"] = constants
    return json.dumps(d, indent=2)


def avaai_export_strategies_json(strategies: List[StrategyInfo]) -> str:
    arr = []
    for s in strategies:
        arr.append({
            "strategy_id": s.strategy_id,
            "target": s.target,
            "token": s.token,
            "allocated": s.allocated,
            "harvested": s.harvested,
            "cap_bps": s.cap_bps,
            "active": s.active,
            "added_at_block": s.added_at_block,
        })
    return json.dumps(arr, indent=2)


def avaai_load_simulator_from_json(path: str) -> Optional[AvaAISimulator]:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        sim = AvaAISimulator()
        if "total_deposited" in data:
            sim._total_deposited = data["total_deposited"]
        if "total_withdrawn" in data:
            sim._total_withdrawn = data["total_withdrawn"]
        if "total_yield" in data:
            sim._total_yield = data["total_yield"]
        if "positions" in data:
            sim._positions = data["positions"]
        if "strategies" in data:
            sim._strategies = {int(k): v for k, v in data["strategies"].items()}
        return sim
    except Exception:
        return None


def avaai_save_simulator_to_json(sim: AvaAISimulator, path: str) -> bool:
    try:
        data = {
            "total_deposited": sim._total_deposited,
            "total_withdrawn": sim._total_withdrawn,
            "total_yield": sim._total_yield,
            "positions": sim._positions,
            "strategies": sim._strategies,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# CLI: simulate
# -----------------------------------------------------------------------------
def cmd_simulate(client: FundManagerAIClient, args: argparse.Namespace, log: logging.Logger) -> int:
    sim = AvaAISimulator()
    sim.add_strategy("0x" + "a" * 40, "0x" + "b" * 40, 5000)
    sim.deposit_sim("0x" + "c" * 40, "0x" + "b" * 40, 1000 * 10**18, 10)
    sim.credit_yield_sim("0x" + "c" * 40, "0x" + "b" * 40, 50 * 10**18)
    stats = sim.get_stats_sim()
    log.info("Simulated stats: deposited=%s withdrawn=%s yield=%s strategies=%s",
             stats.total_deposited, stats.total_withdrawn, stats.total_yield_harvested, stats.strategy_count)
    return 0


# -----------------------------------------------------------------------------
# CLI: export
# -----------------------------------------------------------------------------
def cmd_export(client: FundManagerAIClient, args: argparse.Namespace, log: logging.Logger) -> int:
    stats = client.get_global_stats()
    if not stats:
        log.error("Could not fetch stats")
        return 1
    perf_bps, dep_bps = client.get_fee_config()
    constants = client.get_constants_bundle()
    out = avaai_export_stats_json(stats, (perf_bps, dep_bps), constants)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        log.info("Wrote %s", args.output)
    else:
        print(out)
    return 0


# -----------------------------------------------------------------------------
# Extra format and validation
# -----------------------------------------------------------------------------
def avaai_format_bps(bps: int) -> str:
    return f"{bps} bps ({bps_to_percent(bps)})"


def avaai_wei_to_eth(wei: int) -> str:
    return wei_to_human(wei, 18)


def avaai_eth_to_wei(eth: str) -> int:
    return human_to_wei(eth, 18)


def avaai_ensure_config_dir() -> str:
    Path(AVAAI_CONFIG_DIR).mkdir(parents=True, exist_ok=True)
    return AVAAI_CONFIG_DIR


def avaai_config_path() -> str:
    return os.path.join(AVAAI_CONFIG_DIR, "config.json")


def avaai_default_contract_env() -> str:
    return AVAAI_CONTRACT_ADDRESS_ENV


def avaai_default_rpc_env() -> str:
    return AVAAI_RPC_ENV


def avaai_chain_name(chain_id: int) -> str:
    if chain_id == 1:
        return "mainnet"
    if chain_id == 11155111:
        return "sepolia"
    return f"chain_{chain_id}"


def avaai_parse_chain_id(s: str) -> Optional[int]:
    s = s.strip().lower()
    if s in ("mainnet", "1", "eth"):
        return 1
    if s in ("sepolia", "11155111"):
        return 11155111
    try:
        return int(s)
    except ValueError:
        return None


def avaai_help_text_stats() -> str:
    return "Show global stats (deposited, withdrawn, yield, strategies, fees) from the FundManagerAI contract."


def avaai_help_text_strategies() -> str:
    return "List all strategies (id, target, token, allocated, harvested, cap_bps, active)."


def avaai_help_text_tokens() -> str:
    return "List allowed token addresses from the contract."


def avaai_help_text_position() -> str:
    return "Show deposit balance and claimable yield per token for a user."


def avaai_help_text_deposit() -> str:
    return "Submit a deposit transaction (requires private key and token approval)."

