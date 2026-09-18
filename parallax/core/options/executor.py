"""Options execution — resolve legs to Dhan contracts, compute margin, and
place orders (dry-run safe by default)."""
from __future__ import annotations

from parallax.adapters.market_data.dhan_options import resolve_contract
from parallax.core.options.contracts import OptionContract, OptionStrategy


class OptionsExecutor:
    def __init__(self, broker, config=None):
        self.broker = broker        # DhanBroker (dry_run=True => no orders)
        self.config = config

    def resolve(self, strategy: OptionStrategy) -> list[tuple[OptionContract, str]]:
        """[(contract, side)] resolved for every leg."""
        out = []
        for leg in strategy.legs:
            c = leg.contract
            if c.security_id and c.trading_symbol:
                contract = c
            else:
                contract = resolve_contract(c.symbol, c.strike, c.option_type, c.expiry)
            if contract is None:
                raise RuntimeError(f"cannot resolve {c.symbol} {c.strike} {c.option_type}")
            out.append((contract, "BUY" if leg.side > 0 else "SELL"))
        return out

    def margin(self, strategy: OptionStrategy) -> float:
        """Total margin across all legs (Dhan margin calculator)."""
        total = 0.0
        for contract, side in self.resolve(strategy):
            for leg in strategy.legs:
                if leg.contract.strike == contract.strike and leg.contract.option_type == contract.option_type:
                    total += self.broker.option_margin(contract, side, leg.quantity, leg.premium)
                    break
        return total

    def execute(self, strategy: OptionStrategy, order_type: str = "MARKET"):
        """Place every leg; returns list[OrderAck].  dry_run => all 'DRY' acks."""
        acks = []
        for contract, side in self.resolve(strategy):
            for leg in strategy.legs:
                if leg.contract.strike == contract.strike and leg.contract.option_type == contract.option_type:
                    acks.append(self.broker.place_option_order(
                        contract, side, leg.quantity, order_type, leg.premium))
                    break
        return acks
