"""Debug: test _flow_only directly."""
import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
from data.broker.schwab_auth import get_schwab_client
from aggressive.deep_analyzer import DeepAnalyzer

c = get_schwab_client()
da = DeepAnalyzer()
da.set_context(existing_positions=[], spy_df=None, vix=24.5, schwab_client=c, price_data={})

test_syms = ['AAPL', 'NVDA', 'META', 'BAC', 'CMG', 'AAL', 'SOFI', 'MSFT', 'AMZN', 'JPM']
for sym in test_syms:
    flow = {
        'symbol': sym,
        'direction': 'CALL',
        'signal_strength': 7,
        'cp_ratio': 2.0,
        'total_premium': 1000000,
        'opening_pct': 50,
    }
    try:
        result = da.analyze(sym, None, None, flow, None)
        if result:
            print(f"  {sym}: score={result['composite']:.0f} conv={result['conviction']}")
        else:
            print(f"  {sym}: NONE returned")
    except Exception as e:
        print(f"  {sym}: ERROR: {e}")