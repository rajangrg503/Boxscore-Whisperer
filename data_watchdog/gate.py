"""The one call every batch_cache_*.py script makes before its main
loop starts writing to data_cache/.

This is NOT a try/except fallback layered into the existing fetch
logic -- it's a separate, blocking pre-flight check. A validation
failure here raises WatchdogFailure uncaught, which crashes that batch
script loudly (nonzero exit code, a logged alert) before it ever
touches data_cache/ for that endpoint. Existing cache files are left
completely untouched either way.
"""

from data_watchdog import runner


class WatchdogFailure(Exception):
    """Raised when an endpoint fails its pre-flight schema/row-count
    check. Intentionally left uncaught by callers -- a batch script
    should crash loudly here, not silently skip to a fallback."""


def require_valid(endpoint_key: str) -> None:
    result = runner.check(endpoint_key)
    if not result.passed:
        raise WatchdogFailure(
            f"{endpoint_key}: {result.reason} -- refusing to run this batch job. "
            f"data_cache/ for this endpoint has NOT been touched."
        )
