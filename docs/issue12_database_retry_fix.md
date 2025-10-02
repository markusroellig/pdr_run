# Issue #12: Database Connection Loss During Parallel Execution - Fix Summary

## Problem Description

During parallel execution of computational grids with `pdr_run`, jobs were failing midway through processing with MySQL connection errors:

- **Error**: Lost connection to MySQL server
- **Additional symptoms**: SSL protocol violations (EOF errors)
- **Impact**: Jobs fail approximately halfway through large computational grids
- **Root cause**: Long-running parallel workers experience transient database connection issues due to network instability, timeouts, or SSL errors

## Solution

Implemented a robust retry mechanism with exponential backoff to handle transient database connection failures.

### Key Changes

#### 1. Retry Decorator (`pdr_run/database/queries.py`)

Added a new `retry_on_db_error` decorator that:
- Retries database operations on transient connection errors
- Implements exponential backoff (default: 1s, 2s, 4s, 8s, 16s)
- Handles common error patterns:
  - Lost connection
  - SSL/EOF errors
  - Connection timeouts
  - Broken pipe
  - Connection refused
  - Server gone away
- Cleans up stale sessions before retry
- Limits retries to avoid infinite loops (default: 5 attempts)

```python
@retry_on_db_error(max_retries=5, initial_delay=1.0, backoff=2.0)
def database_operation():
    # Operation that may experience transient failures
    pass
```

#### 2. Enhanced Database Functions

Applied retry logic to critical database operations:

- **`get_or_create()`**: Creating/retrieving database entries (used extensively during job setup)
- **`update_job_status()`**: Updating job status during execution
- **`get_model_name_id()`**: Retrieving model identifiers
- **`get_model_info_from_job_id()`**: Retrieving job metadata
- **`retrieve_job_parameters()`**: Fetching job parameters

### Testing

Created comprehensive test suite (`pdr_run/tests/database/test_retry_logic.py`) covering:

1. **Retry on connection loss**: Verifies operations retry on "Lost connection" errors
2. **Retry on SSL errors**: Verifies operations retry on SSL/EOF errors
3. **Retry exhaustion**: Verifies errors are raised after max retries
4. **Non-retryable errors**: Verifies non-transient errors (e.g., ValueError) are not retried
5. **Immediate success**: Verifies no unnecessary retries on successful operations
6. **Function-specific tests**: Tests for each enhanced database function

**Test Results**: All 46 database tests pass ✓

## Benefits

1. **Resilience**: Jobs can now recover from transient network issues automatically
2. **Robustness**: Long-running parallel jobs are more reliable
3. **Transparency**: Clear logging of retry attempts for debugging
4. **Non-breaking**: Fully backward compatible with existing code
5. **Configurable**: Retry parameters can be adjusted per operation

## Usage

The fix is transparent to users - no code changes required. The retry logic automatically activates when database connection issues occur.

### Logging

When retries occur, you'll see warnings like:

```
WARNING: Database connection error in update_job_status (attempt 1/6): Lost connection to MySQL server. Retrying in 1.0s...
WARNING: Database connection error in update_job_status (attempt 2/6): Lost connection to MySQL server. Retrying in 2.0s...
```

After successful retry:
```
DEBUG: Successfully completed update_job_status after 3 attempts
```

## Implementation Details

### Error Detection

The decorator detects retryable errors by checking for keywords in exception messages:
- `lost connection`
- `connection closed`
- `timeout`
- `eof`
- `ssl`
- `broken pipe`
- `connection refused`
- `can't connect`
- `gone away`

### Exponential Backoff

Default configuration:
- Initial delay: 1.0 seconds
- Backoff multiplier: 2.0
- Max retries: 5
- Maximum total wait time: ~31 seconds (1 + 2 + 4 + 8 + 16)

This prevents overwhelming the database server while giving enough time for transient issues to resolve.

### Session Cleanup

Before each retry, the decorator attempts to:
1. Rollback any uncommitted transactions
2. Clean up stale session state
3. Allow SQLAlchemy's connection pool to recycle the connection

## Related Issues

This fix addresses:
- MySQL connection timeouts during long-running jobs
- SSL protocol violations (EOF errors)
- Network instability between client and database server
- Connection pool exhaustion under heavy parallel load

## Future Enhancements

Potential improvements:
1. Configurable retry parameters via environment variables
2. Metrics tracking for retry statistics
3. Circuit breaker pattern for persistent connection issues
4. Connection health monitoring and proactive recycling

## Testing Recommendations

To verify the fix works in your environment:

```bash
# Run the retry logic tests
python -m pytest pdr_run/tests/database/test_retry_logic.py -v

# Run all database tests
python -m pytest pdr_run/tests/database/ -v

# Test with a parallel computational grid (as reported in issue #12)
pdr_run --config default.yaml --parallel --workers=3 \
        --model-name test_nochemheat \
        --dens 65 70 --chi 0 -10 \
        --mass -15 -10 -5 0 5 10
```

## References

- **Issue**: https://github.com/markusroellig/pdr_run/issues/12
- **SQLAlchemy Error Handling**: https://docs.sqlalchemy.org/en/20/core/exceptions.html
- **Modified Files**:
  - `pdr_run/database/queries.py` (retry decorator and enhanced functions)
  - `pdr_run/tests/database/test_retry_logic.py` (new test suite)
