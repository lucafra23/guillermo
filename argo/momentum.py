import pandas as pd
from .base import BaseScanner
from argo.models import Signal, Recommendation

# This is a conceptual example. A real implementation would need a data source.
# For simplicity, we'll generate some fake data.
import numpy as np


class MomentumScanner(BaseScanner):
    """
    A simple scanner that looks for instruments with strong upward momentum.
    """
    def run(self):
        print("Running MomentumScanner...")
        
        # In a real scenario, you would fetch historical data for each instrument.
        # For example, using the Nautilus historical data client.
        # from nautilus_trader.test_kit.providers import TestInstrumentProvider
        
        for instrument_id in self.instruments:
            print(f"  - Scanning {instrument_id}...")
            
            # FAKE DATA: Create a fake price series with a strong trend
            prices = pd.Series(1.1 + np.linspace(0, 0.05, 100) + np.random.normal(0, 0.001, 100))
            
            # ANALYSIS: Calculate a simple momentum score (e.g., 50-period return)
            momentum_period = self.params.get('momentum_period', 50)
            momentum = (prices.iloc[-1] / prices.iloc[-momentum_period]) - 1
            
            # THRESHOLD: Check if momentum exceeds a threshold
            threshold = self.params.get('threshold', 0.03) # 3% return
            
            if momentum > threshold:
                print(f"    -> Strong bullish signal found for {instrument_id} (Momentum: {momentum:.2%})")
                
                # 1. CREATE SIGNAL
                signal = Signal.objects.create(
                    scanner_id=1, # Assuming a Scanner with ID=1 exists for this class
                    instrument_id=str(instrument_id),
                    signal_type=Signal.SignalType.BULLISH,
                    confidence=min(1.0, momentum / (threshold * 2)), # Normalize confidence
                    details={'momentum_score': momentum, 'period': momentum_period}
                )
                
                # 2. CREATE RECOMMENDATION
                # Find the EMACross strategy in the DB
                ema_cross_strategy = self.get_strategy_model('argo.nautilus.strategies.EMACross')
                
                Recommendation.objects.create(
                    signal=signal,
                    recommended_strategy=ema_cross_strategy,
                    recommended_params={"fast_ema": 5, "slow_ema": 20, "trade_size": 10000},
                    justification=f"Strong upward momentum of {momentum:.2%} detected over {momentum_period} periods. An EMA cross strategy is suitable for trend-following."
                )