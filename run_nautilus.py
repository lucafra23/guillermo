# ---
# jupyter:
#   jupytext:
#     formats: py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# Note: Use the jupytext python package to be able to open this python file in jupyter as a notebook.
# Also run `jupytext-config set-default-viewer` to open jupytext python files as notebooks by default.

# %%
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
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.live.config import LiveDataEngineConfig
from nautilus_trader.live.config import RoutingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model import TraderId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig
from argo.strategies.one_cond import SimpleConditionsConfig
from argo.strategies.one_cond import SimpleConditionsStrategy


IB_HOST = os.getenv("IB_EXAMPLE_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_EXAMPLE_PORT", "4001"))
IBG_DATA_CLIENT_ID = int(os.getenv("IB_EXAMPLE_DATA_CLIENT_ID", "1"))
IBG_EXEC_CLIENT_ID = int(os.getenv("IB_EXAMPLE_EXEC_CLIENT_ID", "2"))
TWS_ACCOUNT = os.getenv("TWS_ACCOUNT", "DUO749192")


from nautilus_trader.adapters.interactive_brokers.config import DockerizedIBGatewayConfig
from nautilus_trader.adapters.interactive_brokers.gateway import DockerizedIBGateway

gateway_config = DockerizedIBGatewayConfig(
    trading_mode="paper",      # "paper" or "live"
    timeout=300,               # Startup timeout in seconds
)
# This may take a short while to start up, especially the first time
gateway = DockerizedIBGateway(config=gateway_config, )
gateway.start()
# Confirm you are logged in
print(gateway.is_logged_in(gateway.container))
# Inspect the logs
print(gateway.container.logs())

# %%
es_contract = IBContract(
    secType="FUT",
    exchange="CME",
    localSymbol="ESM6",
    lastTradeDateOrContractMonth="20260618",
)

contracts = [es_contract]
tradable_instrument_id = "ESM6.CME"


# Configure the trading node
instrument_provider = InteractiveBrokersInstrumentProviderConfig(
    load_contracts=frozenset(contracts),
    symbology_method=SymbologyMethod.IB_SIMPLIFIED,
)

config_node = TradingNodeConfig(
    trader_id=TraderId("CONDITIONS-TESTER-001"),
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="INFO",
        log_file_name=datetime.datetime.strftime(
            datetime.datetime.now(tz=datetime.UTC),
            "%Y-%m-%d_%H-%M",
        )
        + "_simple_conditions_test.log",
        log_directory="./logs/",
        print_config=True,
    ),
    data_clients={
        IB: InteractiveBrokersDataClientConfig(
            market_data_type=IBMarketDataTypeEnum.DELAYED_FROZEN,
            instrument_provider=instrument_provider,
            use_regular_trading_hours=False,
            dockerized_gateway=gateway_config
        ),
    },
    exec_clients={
        IB: InteractiveBrokersExecClientConfig(
            ibg_client_id=IBG_EXEC_CLIENT_ID,
            account_id=TWS_ACCOUNT,
            instrument_provider=instrument_provider,
            routing=RoutingConfig(
                default=True,
            ),
            dockerized_gateway=gateway_config
        ),
    },
    data_engine=LiveDataEngineConfig(
        time_bars_timestamp_on_close=False,
        validate_data_sequence=True,
        time_bars_build_with_no_updates=False,
    ),
    timeout_connection=90.0,
    timeout_reconciliation=5.0,
    timeout_portfolio=5.0,
    timeout_disconnection=5.0,
    timeout_post_stop=10.0,
)

strat_config = SimpleConditionsConfig(
    tradable_instrument_id=tradable_instrument_id,
    manage_stop=True,
    market_exit_max_attempts=400,
    market_exit_time_in_force=TimeInForce.DAY,
    market_exit_reduce_only=False,
)
strategy = SimpleConditionsStrategy(config=strat_config)

# Instantiate the node with a configuration
node = TradingNode(config=config_node)

# Add your strategies and modules
node.trader.add_strategy(strategy)

# Register your client factories with the node
node.add_data_client_factory(IB, InteractiveBrokersLiveDataClientFactory)
node.add_exec_client_factory(IB, InteractiveBrokersLiveExecClientFactory)
node.build()

exec_engine = node.kernel.exec_engine
default_client_id = exec_engine.default_client
if default_client_id is None:
    raise RuntimeError("Expected an Interactive Brokers execution client to be registered")
strategy.exec_client = exec_engine._clients[default_client_id]

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()