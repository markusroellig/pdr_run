#!/usr/bin/env python
"""
Demonstration script showing database retry logic in action.

This script simulates database connection failures and shows how the retry
mechanism handles them gracefully.
"""

import logging
from sqlalchemy.exc import OperationalError
from pdr_run.database.queries import retry_on_db_error

# Set up logging to see retry messages
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def demo_successful_retry():
    """Demonstrate successful retry after connection failures."""
    print("\n" + "="*70)
    print("DEMO 1: Successful Retry After Connection Failures")
    print("="*70)

    call_count = 0

    @retry_on_db_error(max_retries=3, initial_delay=0.5, backoff=2.0)
    def simulate_flaky_connection():
        nonlocal call_count
        call_count += 1

        logger.info(f"Attempt {call_count}: Trying database operation...")

        if call_count < 3:
            logger.warning(f"Attempt {call_count}: Simulating connection failure")
            raise OperationalError(
                "Lost connection to MySQL server during query",
                None,
                None
            )

        logger.info(f"Attempt {call_count}: Success!")
        return {"status": "success", "attempts": call_count}

    try:
        result = simulate_flaky_connection()
        print(f"\n✓ Operation completed successfully after {result['attempts']} attempts")
        return True
    except Exception as e:
        print(f"\n✗ Operation failed: {e}")
        return False


def demo_ssl_error_retry():
    """Demonstrate retry on SSL/EOF errors."""
    print("\n" + "="*70)
    print("DEMO 2: Retry on SSL/EOF Errors")
    print("="*70)

    call_count = 0

    @retry_on_db_error(max_retries=2, initial_delay=0.3, backoff=1.5)
    def simulate_ssl_error():
        nonlocal call_count
        call_count += 1

        logger.info(f"Attempt {call_count}: Connecting to database...")

        if call_count == 1:
            logger.warning(f"Attempt {call_count}: SSL protocol violation (EOF)")
            raise OperationalError(
                "SSL connection has been closed unexpectedly: EOF",
                None,
                None
            )

        logger.info(f"Attempt {call_count}: Connection established!")
        return {"status": "connected", "attempts": call_count}

    try:
        result = simulate_ssl_error()
        print(f"\n✓ Connection established after {result['attempts']} attempts")
        return True
    except Exception as e:
        print(f"\n✗ Connection failed: {e}")
        return False


def demo_max_retries_exhausted():
    """Demonstrate behavior when max retries are exhausted."""
    print("\n" + "="*70)
    print("DEMO 3: Max Retries Exhausted (Error Raised)")
    print("="*70)

    call_count = 0

    @retry_on_db_error(max_retries=2, initial_delay=0.2, backoff=1.5)
    def simulate_persistent_failure():
        nonlocal call_count
        call_count += 1

        logger.info(f"Attempt {call_count}: Trying to connect...")
        logger.error(f"Attempt {call_count}: Connection refused")

        raise OperationalError(
            "Can't connect to MySQL server on 'unreachable-host:3306'",
            None,
            None
        )

    try:
        simulate_persistent_failure()
        print("\n✗ Unexpected success")
        return False
    except OperationalError as e:
        print(f"\n✓ Correctly raised error after {call_count} attempts")
        print(f"   Final error: {str(e)[:80]}...")
        return True


def demo_non_retryable_error():
    """Demonstrate that non-retryable errors are not retried."""
    print("\n" + "="*70)
    print("DEMO 4: Non-Retryable Errors (No Retry)")
    print("="*70)

    call_count = 0

    @retry_on_db_error(max_retries=3, initial_delay=0.2, backoff=2.0)
    def simulate_validation_error():
        nonlocal call_count
        call_count += 1

        logger.info(f"Attempt {call_count}: Validating input...")
        logger.error(f"Attempt {call_count}: Invalid parameter detected")

        raise ValueError("Invalid parameter: job_id must be positive")

    try:
        simulate_validation_error()
        print("\n✗ Unexpected success")
        return False
    except ValueError as e:
        print(f"\n✓ Correctly raised error immediately (only {call_count} attempt)")
        print(f"   Error: {e}")
        return True


def main():
    """Run all demonstrations."""
    print("\n" + "="*70)
    print("DATABASE RETRY LOGIC DEMONSTRATION")
    print("Fix for Issue #12: Database Connection Loss During Parallel Execution")
    print("="*70)

    results = []

    # Run all demos
    results.append(demo_successful_retry())
    results.append(demo_ssl_error_retry())
    results.append(demo_max_retries_exhausted())
    results.append(demo_non_retryable_error())

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Demos passed: {sum(results)}/{len(results)}")

    if all(results):
        print("\n✓ All demonstrations completed successfully!")
        print("\nThe retry logic will automatically handle transient database")
        print("connection failures during parallel execution, making your jobs")
        print("more resilient to network issues, timeouts, and SSL errors.")
    else:
        print("\n✗ Some demonstrations failed")
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
