# run_node.py
import os
import sys
import asyncio

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.adapters.architect_ax import AX

def setup_django():
    """Initializes Django, must be run before importing any project modules."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings_argo')
    import django
    django.setup()

setup_django()

from nautilus_trader.config import TradingNodeConfig, LiveExecEngineConfig
from nautilus_trader.live.node import TradingNode

from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersDataClientConfig,
)
from nautilus_trader.examples.strategies.bb_mean_reversion import BBMeanReversion
from nautilus_trader.examples.strategies.bb_mean_reversion import BBMeanReversionConfig

# Now it's safe to import from your Django apps
from argo.scanners.energy_dislocation import EnergyDislocationConfig, EnergyDislocationScanner
from argo.strategies.ema_cross import EMACrossStrategyConfig, EMACrossStrategy

instrument_id = InstrumentId.from_str(f"EURUSD-PERP.{AX}")

async def main():
    # 1. Configure the Execution Client
    # Default ports: TWS Live = 7496, TWS Paper = 7497, IB Gateway Live = 4001, IB Gateway Paper = 4002
    exec_config = InteractiveBrokersExecClientConfig(
        ibg_host="127.0.0.1",
        ibg_port=4002,  # Port for your TWS/IB Gateway instance (using 4002 for Paper Gateway)
        account_id="DU1234567",  # Your IB Account ID
    )

    # 2. Configure a data client (optional, but often needed for strategies)
    data_config = InteractiveBrokersDataClientConfig(
        ibg_host="127.0.0.1",
        ibg_port=4002,
    )

    # 4. Configure an example strategy actor (e.g., EMACrossStrategy)
    ema_strategy_config = EMACrossStrategyConfig(
        actor_path="argo.strategies.ema_cross.EMACrossStrategy",
        config_path="argo.strategies.ema_cross.EMACrossStrategyConfig",
        instrument_id="EUR/USD.CASH.IDEALPRO",  # Example instrument
        bar_type="1-MINUTE-BAR-ASK-BID-LAST",  # Example bar type
        fast_ema_period=10,
        slow_ema_period=20,
        trade_size=10000.0,
    )

    strategy = BBMeanReversion(
        config=BBMeanReversionConfig(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=Decimal(1),
            bb_period=20,
            bb_std=2.0,
            rsi_period=14,
            rsi_buy_threshold=0.30,
            rsi_sell_threshold=0.70,
        ),
    )

    node.trader.add_strategy(strategy)
    # 5. Add the actor configs to the TradingNodeConfig
    node_config = TradingNodeConfig(
        trader_id="IB-TRADER-01",
        # Pass the config object and client ID as a tuple for each client
        exec_clients={"IBKR": (exec_config, 1)},  # Registered under venue name "IBKR"
        data_clients={"IBKR": (data_config, 2)},  # Data client needs a distinct client_id
        exec_engine=LiveExecEngineConfig(),
        actors=[ema_strategy_config],  # Add both scanner and strategy
    )

    # 5. Instantiate and run node
    node = TradingNode(config=node_config)

    # 6. Run the node
    await node.run_async()

if __name__ == "__main__":
    asyncio.run(main())