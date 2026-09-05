"""data_watchdog -- validates nba_api endpoint responses against
engine.schemas.ENDPOINT_CONTRACTS before a batch refresh script is
allowed to write anything into data_cache/.

Named data_watchdog, not watchdog, to avoid colliding with the
third-party PyPI "watchdog" filesystem-monitoring package (which
Streamlit's own startup log suggests installing for faster reload).
"""
