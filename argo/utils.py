# --- Load Strategies from Django Database ---
from nautilus_trader.live.node import TradingNode

def discover_strategies():
    return []

def load_strategies_from_db(node: TradingNode):
    """
    Finds the active portfolio in the database and loads its strategy instances
    into the trading node.
    try:
        active_portfolio = Portfolio.objects.get(is_active=True)
        print(f"Loading active portfolio: '{active_portfolio.name}'")
    except Portfolio.DoesNotExist:
        print("No active portfolio found in the database. No strategies will be loaded.")
        return
    except Portfolio.MultipleObjectsReturned:
        print("Error: Multiple active portfolios found. Please activate only one.")
        return

    for instance in active_portfolio.strategy_instances.filter(is_active=True):
        print(f"  - Loading strategy: {instance}")
        try:
            module_path, class_name = instance.strategy_model.class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            strategy_class = getattr(module, class_name)

            # Example: Assuming all strategies need a bar_type. This could be stored in params.
            bar_type = BarType.from_str("1-MINUTE-BAR-ASK-BID-LAST")

            config = instance.params
            strategy = strategy_class(instrument_id=instance.instrument_id, bar_type=bar_type, **config)
            node.add_strategy(strategy)
        except Exception as e:
            print(f"    [ERROR] Failed to load strategy instance {instance.id}: {e}")
            """
    pass