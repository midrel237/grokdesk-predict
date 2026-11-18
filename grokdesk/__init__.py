"""GROK DESK — an autonomous pump.fun trading desk built out of small agents.

Six desks (radar, research, execution, risk, audit, treasury) and one head of
desk, GROK CORE, that reads every desk once an hour and decides who gets more
budget and who gets fired. Fired means the audit desk rewrites that agent's
prompt from the last window of its own numbers, so the desk keeps tuning itself.

Everything here is paper trading. No wallet, no signing, no network required.
"""
__version__ = "0.1.0"
