"""Pure schema validation -- no network calls, no I/O. Fully
unit-testable with fixture DataFrames (see
tests/test_data_watchdog_validators.py), which is exactly what proves
this would have caught the BoxScoreTraditionalV2 deprecation
automatically: a fixture shaped like V2's actual dead response (0 rows,
real column names) fails validate_schema() the same way a live V2 call
would have -- no live API call needed to prove the point.
"""

from dataclasses import dataclass
from typing import Optional, Set

import pandas as pd

from engine.schemas import EndpointContract


@dataclass
class ValidationResult:
    passed: bool
    reason: str
    row_count: int = 0
    missing_columns: Optional[Set[str]] = None


def validate_schema(df: Optional[pd.DataFrame], contract: EndpointContract) -> ValidationResult:
    if df is None:
        return ValidationResult(passed=False, reason="query returned None, not a DataFrame")

    missing = contract.required_columns - set(df.columns)
    if missing:
        return ValidationResult(
            passed=False,
            reason=f"missing required columns: {sorted(missing)}",
            row_count=len(df),
            missing_columns=missing,
        )

    if len(df) < contract.min_rows:
        return ValidationResult(
            passed=False,
            reason=(
                f"got {len(df)} row(s), expected at least {contract.min_rows} for a "
                f"known-good query -- endpoint may have stopped returning data"
            ),
            row_count=len(df),
        )

    return ValidationResult(passed=True, reason="ok", row_count=len(df))
