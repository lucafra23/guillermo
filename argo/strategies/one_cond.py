import datetime
import os
import threading
import time

from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.common import IBContract
from nautilus_trader.adapters.interactive_brokers.common import IBOrderTags
from nautilus_trader.adapters.interactive_brokers.config import IBMarketDataTypeEnum
from nautilus_trader.adapters.interactive_brokers.config import InteractiveBrokersDataClientConfig
from nautilus_trader.adapters.interactive_brokers.config import InteractiveBrokersExecClientConfig
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.config import SymbologyMethod
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig




# %%
class SimpleConditionsConfig(StrategyConfig, frozen=True):
    tradable_instrument_id: str | None = "ESM6.CME"


class SimpleConditionsStrategy(Strategy):
    def __init__(self, config: SimpleConditionsConfig) -> None:
        super().__init__(config)
        self.tradable_instrument_id = config.tradable_instrument_id
        self.exec_client = None

    def on_order_canceled(self, event):
        self.log.info(f"Order canceled: {event}")

    def on_order_pending_cancel(self, event):
        self.log.info(f"Order pending cancel: {event}")

    def on_start(self) -> None:
        for instrument in self.cache.instruments():
            if str(instrument.id) == self.tradable_instrument_id:
                self.test_time_condition_order(instrument)
                self.test_price_condition_order(instrument)

    def test_price_condition_order(self, instrument):
        contract_id = instrument.info.get("contract", {}).get("conId", 0)
        price_condition = {
            "type": "price",  # Use actual ES contract ID
            "conId": contract_id,
            "exchange": "CME",
            "isMore": True,
            "price": 6000.0,
            "triggerMethod": 0,
            "conjunction": "and",
        }
        order = LimitOrder(
            trader_id=self.trader_id,
            strategy_id=self.id,
            instrument_id=instrument.id,
            client_order_id=self.order_factory.generate_client_order_id(),
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(1),
            price=instrument.make_price(5950),
            init_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
            time_in_force=TimeInForce.GTC,
            tags=[IBOrderTags(conditions=[price_condition]).value],
        )
        self.submit_order(order)

    def test_time_condition_order(self, instrument):
        time_str = (datetime.datetime.now() + datetime.timedelta(minutes=5)).strftime(
            "%Y%m%d-%H:%M:%S",
        )
        time_condition = {
            "type": "time",
            "time": time_str,
            "isMore": True,
            "conjunction": "and",
        }
        order = LimitOrder(
            trader_id=self.trader_id,
            strategy_id=self.id,
            instrument_id=instrument.id,
            client_order_id=self.order_factory.generate_client_order_id(),
            order_side=OrderSide.SELL,
            quantity=instrument.make_qty(1),
            price=instrument.make_price(6100),
            init_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
            time_in_force=TimeInForce.GTC,
            tags=[IBOrderTags(conditions=[time_condition]).value],
        )
        self.submit_order(order)

    def _cancel_all_cached_orders(self, reason: str) -> None:
        instrument_id = InstrumentId.from_str(self.tradable_instrument_id)
        orders_open = self.cache.orders_open(instrument_id=instrument_id)
        orders_inflight = self.cache.orders_inflight(instrument_id=instrument_id)
        total_orders = len(orders_open) + len(orders_inflight)
        if total_orders == 0:
            return

        if self.exec_client is None:
            self.log.warning("No execution client is bound for cancel-all handling")
            return

        self.log.info(f"Canceling {total_orders} cached orders for {reason}")
        command = CancelAllOrders(
            trader_id=self.trader_id,
            strategy_id=self.id,
            instrument_id=instrument_id,
            order_side=OrderSide.NO_ORDER_SIDE,
            command_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
        )
        self.exec_client.cancel_all_orders(command)

    def _has_pending_cached_orders(self) -> bool:
        instrument_id = InstrumentId.from_str(self.tradable_instrument_id)
        return bool(
            self.cache.orders_open(instrument_id=instrument_id)
            or self.cache.orders_inflight(instrument_id=instrument_id),
        )
