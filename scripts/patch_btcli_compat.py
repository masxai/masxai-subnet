#!/usr/bin/env python3
"""
Patch installed bittensor-cli for testnet runtime compatibility.

Why this exists:
  - Some testnet runtimes do not expose Swap.AlphaSqrtPrice, while btcli wallet
    overview expects it.
  - Some testnet subnets can produce tempo - blocks_since_last_step <= 0 during
    register, which causes SCALE encoding to fail with:
      "Negative integers not supported"

This script patches the installed bittensor_cli package in the active Python
environment. It is intentionally outside MASXAI core code and is safe to rerun.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _package_root() -> Path:
    spec = importlib.util.find_spec("bittensor_cli")
    if spec is None or spec.origin is None:
        raise SystemExit("bittensor_cli is not installed in this Python environment")
    return Path(spec.origin).resolve().parent


def _replace_once(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text()
    if new in text:
        print(f"ok: {label} already patched")
        return False
    if old not in text:
        raise SystemExit(f"could not patch {label}: expected code not found in {path}")
    path.write_text(text.replace(old, new, 1))
    print(f"patched: {label}")
    return True


def patch_alpha_sqrt_price(root: Path) -> bool:
    path = root / "src" / "bittensor" / "subtensor_interface.py"
    changed = False

    old_single = """        current_sqrt_price = await self.query(
            module="Swap",
            storage_function="AlphaSqrtPrice",
            params=[netuid],
            block_hash=block_hash,
        )
"""
    new_single = """        try:
            current_sqrt_price = await self.query(
                module="Swap",
                storage_function="AlphaSqrtPrice",
                params=[netuid],
                block_hash=block_hash,
            )
        except Exception:
            return Balance.from_tao(1.0)
"""
    changed |= _replace_once(path, old_single, new_single, "get_subnet_price fallback")

    old_map = """        query = await self.substrate.query_map(
            module="Swap",
            storage_function="AlphaSqrtPrice",
            page_size=page_size,
            block_hash=block_hash,
            fully_exhaust=True,
        )
"""
    new_map = """        try:
            query = await self.substrate.query_map(
                module="Swap",
                storage_function="AlphaSqrtPrice",
                page_size=page_size,
                block_hash=block_hash,
                fully_exhaust=True,
            )
        except Exception:
            return {}
"""
    changed |= _replace_once(path, old_map, new_map, "get_subnet_prices fallback")
    return changed


def patch_negative_era(root: Path) -> bool:
    path = root / "src" / "bittensor" / "extrinsics" / "registration.py"
    old = """            validity_period = tempo - blocks_since_last_step
            era_ = {
                "period": validity_period,
                "current": current_block,
            }
"""
    new = """            validity_period = tempo - blocks_since_last_step
            if validity_period <= 0:
                validity_period = tempo if tempo and tempo > 0 else 64
            era_ = {
                "period": validity_period,
                "current": current_block,
            }
"""
    return _replace_once(path, old, new, "registration era clamp")


def main() -> None:
    root = _package_root()
    print(f"bittensor_cli: {root}")
    changed = patch_alpha_sqrt_price(root)
    changed |= patch_negative_era(root)
    print("done: patched" if changed else "done: no changes needed")


if __name__ == "__main__":
    main()
