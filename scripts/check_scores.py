import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.signals.signal_generator import SignalGenerator

gen = SignalGenerator()

# Just score a few top stocks to see the breakdown
for symbol in ["AAPL", "NVDA", "MSFT"]:
    df = gen.load_price_data(symbol)
    spy = gen.load_price_data("SPY")
    xlk = gen.load_price_data("XLK")
    
    result = gen.scorer.score_stock(
        symbol, df, spy, xlk, 20, None, "Technology"
    )
    
    print(f"\n{'='*50}")
    print(f"{symbol} - Composite: {result['composite_score']}")
    print(f"Action: {result['action']}")
    print(f"Sub-scores:")
    for k, v in result['sub_scores'].items():
        print(f"  {k:20s}: {v}")
    print(f"Entry: ${result['trade_params']['entry_price']:.2f}")
    print(f"Stop:  ${result['trade_params']['stop_loss']:.2f}")
    print(f"T1:    ${result['trade_params']['target_1']:.2f}")