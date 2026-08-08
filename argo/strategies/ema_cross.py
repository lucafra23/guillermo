from dataclasses import dataclass, field

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ImportableActorConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.trading.strategy import Strategy as NautilusStrategy




class EMACrossStrategyConfig(ImportableActorConfig):
    """
    Configuration for the EMACrossStrategy.
    """
    instrument_id: str
    bar_type: str
    fast_ema_period: int = 10
    slow_ema_period: int = 20
    trade_size: float = 100.0


class EMACrossStrategy(NautilusStrategy):
    """
    A simple EMA cross strategy that goes long when the fast EMA crosses above
    the slow EMA, and closes the position when the fast EMA crosses below the slow EMA.
    """

    def __init__(self, config: EMACrossStrategyConfig):
        super().__init__(config=config)
        self.config = config

        self.fast_ema = None
        self.slow_ema = None
        self.last_fast_ema = None
        self.last_slow_ema = None
        self.position_open = False

        # Parse instrument_id and bar_type from config
        self._instrument_id = self.instrument_provider.get_instrument(self.config.instrument_id)
        self._bar_type = BarType.from_str(self.config.bar_type)

    def on_start(self):
        self.log.info(
            f"Starting EMACrossStrategy for {self._instrument_id} "
            f"with fast_ema={self.config.fast_ema_period}, slow_ema={self.config.slow_ema_period}"
        )
        self.subscribe_data(self._instrument_id, self._bar_type)

    def on_bar(self, bar: Bar):
        # Ensure the bar is for the instrument this strategy is configured for
        if bar.instrument_id != self._instrument_id.id:
            return

        # Calculate EMAs (simplified for example, a real strategy would use an indicator library)
        if self.fast_ema is None:
            self.fast_ema = bar.close
            self.slow_ema = bar.close
        else:
            self.last_fast_ema = self.fast_ema
            self.last_slow_ema = self.slow_ema
            self.fast_ema = (bar.close * (2 / (self.config.fast_ema_period + 1))) + (self.fast_ema * (1 - (2 / (self.config.fast_ema_period + 1))))
            self.slow_ema = (bar.close * (2 / (self.config.slow_ema_period + 1))) + (self.slow_ema * (1 - (2 / (self.config.slow_ema_period + 1))))

        if self.last_fast_ema is None or self.last_slow_ema is None:
            return  # Not enough data yet

        # Check for cross
        if self.fast_ema > self.slow_ema and self.last_fast_ema <= self.last_slow_ema:
            if not self.position_open:
                self.log.info(f"[{self._instrument_id}] Bullish cross detected. Going long.")
                order = self.order_factory.market(self._instrument_id, OrderSide.BUY, self.config.trade_size, TimeInForce.FOK)
                self.submit_order(order)
                self.position_open = True
        elif self.fast_ema < self.slow_ema and self.last_fast_ema >= self.last_slow_ema:
            if self.position_open:
                self.log.info(f"[{self._instrument_id}] Bearish cross detected. Closing long position.")
                order = self.order_factory.market(self._instrument_id, OrderSide.SELL, self.config.trade_size, TimeInForce.FOK)
                self.submit_order(order)
                self.position_open = False

    def on_stop(self):
        self.log.info(f"Stopping EMACrossStrategy for {self._instrument_id}")