"""Fix indentation in spread close blocks."""
f = open("aggressive/options_executor.py", "r", encoding="utf-8").read()

# Fix first block (debit spread close)
f = f.replace(
    """                # Get current mid for limit price
                    try:
                        lq1 = self.client.get_quote(long_leg["symbol"])
                        lq2 = self.client.get_quote(short_leg["symbol"])
                        mid1 = (lq1.json().get(long_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq1.json().get(long_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                        mid2 = (lq2.json().get(short_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq2.json().get(short_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                        net_credit = mid1 - mid2  # Selling long, buying short
                        limit = str(round(abs(net_credit) * 0.90, 2))  # Accept 10% worse than mid
                    except Exception:
                        limit = "0.05"
                    order = (OrderBuilder()
                    .set_order_type(OrderType.NET_CREDIT)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .build())""",
    """                # Get current mid for limit price
                try:
                    lq1 = self.client.get_quote(long_leg["symbol"])
                    lq2 = self.client.get_quote(short_leg["symbol"])
                    mid1 = (lq1.json().get(long_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq1.json().get(long_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                    mid2 = (lq2.json().get(short_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq2.json().get(short_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                    net_credit = mid1 - mid2
                    limit = str(round(abs(net_credit) * 0.90, 2))
                except Exception:
                    limit = "0.05"
                order = (OrderBuilder()
                    .set_order_type(OrderType.NET_CREDIT)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .build())""")

# Fix second block (credit spread close)
f = f.replace(
    """                # Get current mid for limit price
                    try:
                        lq1 = self.client.get_quote(short_leg["symbol"])
                        lq2 = self.client.get_quote(long_leg["symbol"])
                        mid1 = (lq1.json().get(short_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq1.json().get(short_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                        mid2 = (lq2.json().get(long_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq2.json().get(long_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                        net_debit = mid1 - mid2  # Buying short, selling long
                        limit = str(round(abs(net_debit) * 1.10, 2))  # Pay up to 10% more than mid
                    except Exception:
                        limit = "5.00"
                    order = (OrderBuilder()
                    .set_order_type(OrderType.NET_DEBIT)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .build())""",
    """                # Get current mid for limit price
                try:
                    lq1 = self.client.get_quote(short_leg["symbol"])
                    lq2 = self.client.get_quote(long_leg["symbol"])
                    mid1 = (lq1.json().get(short_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq1.json().get(short_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                    mid2 = (lq2.json().get(long_leg["symbol"],{}).get("quote",{}).get("bidPrice",0) + lq2.json().get(long_leg["symbol"],{}).get("quote",{}).get("askPrice",0)) / 2
                    net_debit = mid1 - mid2
                    limit = str(round(abs(net_debit) * 1.10, 2))
                except Exception:
                    limit = "5.00"
                order = (OrderBuilder()
                    .set_order_type(OrderType.NET_DEBIT)
                    .set_price(limit)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.DAY)
                    .add_option_leg(OptionInstruction.BUY_TO_CLOSE, short_leg["symbol"], short_leg.get("qty", 1))
                    .add_option_leg(OptionInstruction.SELL_TO_CLOSE, long_leg["symbol"], long_leg.get("qty", 1))
                    .build())""")

open("aggressive/options_executor.py", "w", encoding="utf-8").write(f)

import py_compile
try:
    py_compile.compile("aggressive/options_executor.py", doraise=True)
    print("COMPILE: options_executor.py OK")
except py_compile.PyCompileError as e:
    print(f"ERROR: {e}")