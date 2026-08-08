from dataclasses import dataclass, field

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.common.config import ImportableActorConfig

class EnergyDislocationConfig(ImportableActorConfig):

    """
    Macro Driver: Extreme localized energy swings (WTI/Brent spikes vs. airline, transport, and refinery equities).
    Instrument Group: High-beta Energy Futures vs. Transport/Airline Equities (WTI.NYMEX, BRENT.ICE, AAL.NASDAQ, DAL.NYSE, XOM.NYSE).
    Alpha Concept: Detect sudden directional standard-deviation breaks in front-month Crude futures and scan for delayed orderbook updates or mispricings in high-beta energy consumers to fade or catch momentum.
    """
    energy_futures_id: str
    equity_basket: list[str]
    zscore_threshold: float = 2.5
    lookback: int = 30

class EnergyDislocationScanner(Actor):
    def __init__(self, config: EnergyDislocationConfig) -> None:
        super().__init__(config)
        self._energy_prices: list[float] = []

    def on_start(self) -> None:
        """
        Subscribes to the bar streams for the energy future and the equity basket.
        """
        # Subscribe to the energy future
        self.subscribe_data(self.config.energy_futures_id)
        # Subscribe to all equities in the basket
        for instrument_id in self.config.equity_basket:
            self.subscribe_data(instrument_id)

    def on_bar(self, bar: Bar) -> None:
        inst_id = str(bar.bar_type.instrument_id)
        
        if inst_id == self.config.energy_futures_id:
            self._energy_prices.append(float(bar.close))
            if len(self._energy_prices) > self.config.lookback:
                self._energy_prices.pop(0)
                
            # Calculate rolling Z-score on energy move
            returns = [y - x for x, y in zip(self._energy_prices[:-1], self._energy_prices[1:])]
            if len(returns) >= self.config.lookback - 1:
                mean_ret = sum(returns) / len(returns)
                std_ret = (sum((r - mean_ret)**2 for r in returns) / len(returns)) ** 0.5
                curr_ret = returns[-1]
                
                z_score = (curr_ret - mean_ret) / std_ret if std_ret > 0 else 0
                
                if abs(z_score) >= self.config.zscore_threshold:
                    self.log.warning(f"⚡ MACRO SHOCK: Crude Spike (Z={z_score:.2f}). Scanning equity lag...")
                    # Trigger alert or inspect order book imbalance for equity basket