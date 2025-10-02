#!/usr/bin/env python
"""
Database Traffic Monitoring Test

This script tests the database connection management improvements
and provides detailed metrics on connection usage during grid execution.
"""

import os
import sys
import time
import threading
import mysql.connector
from datetime import datetime

# Set up environment for MySQL
os.environ['PDR_DB_TYPE'] = 'mysql'
os.environ['PDR_DB_HOST'] = 'localhost'
os.environ['PDR_DB_PORT'] = '3306'
os.environ['PDR_DB_DATABASE'] = 'pdr_test'
os.environ['PDR_DB_USERNAME'] = 'pdr_user'
os.environ['PDR_DB_PASSWORD'] = 'pdr_password'

# Use in-memory storage for testing
os.environ['PDR_STORAGE_TYPE'] = 'local'
os.environ['PDR_STORAGE_DIR'] = '/tmp/pdr_test_storage'

# Enable database diagnostics
os.environ['PDR_DB_DIAGNOSTICS'] = 'true'

from pdr_run.core.engine import run_parameter_grid
from pdr_run.database.db_manager import get_db_manager


class MySQLMonitor:
    """Monitor MySQL connection statistics in real-time."""

    def __init__(self):
        self.monitoring = False
        self.stats = []
        self.thread = None

    def get_mysql_stats(self):
        """Query MySQL for current connection stats."""
        try:
            conn = mysql.connector.connect(
                host='127.0.0.1',
                port=3306,
                user='pdr_user',
                password='pdr_password',
                database='pdr_test'
            )
            cursor = conn.cursor(dictionary=True)

            # Get connection counts
            cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
            threads = cursor.fetchone()

            cursor.execute("SHOW STATUS LIKE 'Threads_running'")
            running = cursor.fetchone()

            cursor.execute("SHOW STATUS LIKE 'Connections'")
            total_conns = cursor.fetchone()

            cursor.execute("SHOW STATUS LIKE 'Max_used_connections'")
            max_used = cursor.fetchone()

            # Get process list
            cursor.execute("SHOW PROCESSLIST")
            processes = cursor.fetchall()

            cursor.close()
            conn.close()

            return {
                'timestamp': time.time(),
                'threads_connected': int(threads['Value']),
                'threads_running': int(running['Value']),
                'total_connections': int(total_conns['Value']),
                'max_used_connections': int(max_used['Value']),
                'process_count': len(processes),
                'processes': processes
            }
        except Exception as e:
            return {
                'timestamp': time.time(),
                'error': str(e)
            }

    def monitor_loop(self):
        """Continuously monitor MySQL stats."""
        print("\n" + "="*80)
        print("MYSQL CONNECTION MONITOR - STARTED")
        print("="*80)
        print(f"{'Time':<12} {'Connected':<12} {'Running':<10} {'Total':<10} {'Max Used':<10}")
        print("-"*80)

        while self.monitoring:
            stats = self.get_mysql_stats()
            self.stats.append(stats)

            if 'error' not in stats:
                print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]:<12} "
                      f"{stats['threads_connected']:<12} "
                      f"{stats['threads_running']:<10} "
                      f"{stats['total_connections']:<10} "
                      f"{stats['max_used_connections']:<10}")

            time.sleep(0.5)  # Poll every 500ms

    def start(self):
        """Start monitoring in background thread."""
        self.monitoring = True
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop monitoring and return stats."""
        self.monitoring = False
        if self.thread:
            self.thread.join(timeout=2)
        print("-"*80)
        print("MYSQL CONNECTION MONITOR - STOPPED")
        print("="*80 + "\n")
        return self.stats


def analyze_stats(stats):
    """Analyze collected statistics."""
    if not stats:
        print("No stats collected")
        return

    # Filter out errors
    valid_stats = [s for s in stats if 'error' not in s]

    if not valid_stats:
        print("No valid stats collected")
        return

    print("\n" + "="*80)
    print("DATABASE TRAFFIC ANALYSIS")
    print("="*80)

    # Connection statistics
    connections = [s['threads_connected'] for s in valid_stats]
    running = [s['threads_running'] for s in valid_stats]
    total = [s['total_connections'] for s in valid_stats]
    max_used = [s['max_used_connections'] for s in valid_stats]

    print(f"\n📊 Connection Statistics:")
    print(f"  Monitoring Duration:     {valid_stats[-1]['timestamp'] - valid_stats[0]['timestamp']:.2f} seconds")
    print(f"  Samples Collected:       {len(valid_stats)}")
    print(f"\n🔌 Active Connections:")
    print(f"  Minimum:                 {min(connections)}")
    print(f"  Maximum:                 {max(connections)}")
    print(f"  Average:                 {sum(connections)/len(connections):.2f}")
    print(f"  Final:                   {connections[-1]}")
    print(f"\n⚡ Running Threads:")
    print(f"  Minimum:                 {min(running)}")
    print(f"  Maximum:                 {max(running)}")
    print(f"  Average:                 {sum(running)/len(running):.2f}")
    print(f"\n📈 Cumulative Stats:")
    print(f"  Total Connections Made:  {total[-1] - total[0]}")
    print(f"  Max Used (Session):      {max(max_used)}")

    # Connection leak detection
    baseline_connections = connections[0]
    final_connections = connections[-1]
    leaked_connections = final_connections - baseline_connections

    print(f"\n🔍 Connection Leak Analysis:")
    print(f"  Baseline Connections:    {baseline_connections}")
    print(f"  Final Connections:       {final_connections}")
    print(f"  Difference:              {leaked_connections}")

    if leaked_connections > 0:
        print(f"  ⚠️  WARNING: {leaked_connections} connection(s) may be leaked!")
    else:
        print(f"  ✅ PASS: No connection leaks detected")

    print("="*80 + "\n")


def main():
    """Run the database traffic test."""
    print("\n" + "="*80)
    print("DATABASE CONNECTION TRAFFIC TEST")
    print("Testing MySQL connection management with grid execution")
    print("="*80 + "\n")

    # Initialize database manager with diagnostics
    print("📝 Initializing database manager...")
    db_manager = get_db_manager()
    db_manager.create_tables()
    print("✅ Database initialized\n")

    # Get baseline connection count
    monitor = MySQLMonitor()
    baseline = monitor.get_mysql_stats()
    print(f"📊 Baseline MySQL connections: {baseline.get('threads_connected', 'N/A')}\n")

    # Define small test grid (2x2 = 4 jobs)
    params = {
        'metal': ['100'],
        'dens': ['3.0', '4.0'],
        'mass': ['5.0', '6.0'],
        'chi': ['1.0'],
        'species': ['CO', 'C+'],
        'chemistry': ['umist']
    }

    print(f"🧪 Test Configuration:")
    print(f"  Grid Size:               2x2 = 4 parameter combinations")
    print(f"  Execution Mode:          Parallel (2 workers)")
    print(f"  Database:                MySQL (sandbox)")
    print(f"  Storage:                 Local (/tmp)\n")

    # Start monitoring
    monitor.start()

    # Run the grid
    print("🚀 Starting grid execution...\n")
    start_time = time.time()

    try:
        job_ids = run_parameter_grid(
            params=params,
            model_name='db_traffic_test',
            config=None,  # Use environment variables
            parallel=True,
            n_workers=2
        )
        execution_time = time.time() - start_time

        print(f"\n✅ Grid execution completed in {execution_time:.2f} seconds")
        print(f"📋 Created {len(job_ids)} jobs: {job_ids}\n")

    except Exception as e:
        print(f"\n❌ Grid execution failed: {e}")
        import traceback
        traceback.print_exc()

    # Wait a bit for cleanup
    print("⏳ Waiting for connection cleanup...")
    time.sleep(2)

    # Stop monitoring and analyze
    stats = monitor.stop()
    analyze_stats(stats)

    # Final check
    final = monitor.get_mysql_stats()
    print(f"📊 Final MySQL connections: {final.get('threads_connected', 'N/A')}")

    # Get database diagnostics if enabled
    if hasattr(db_manager, 'get_diagnostics_snapshot'):
        print("\n" + "="*80)
        print("DATABASE MANAGER DIAGNOSTICS")
        print("="*80)
        snapshot = db_manager.get_diagnostics_snapshot(include_events=False)

        if snapshot:
            print(f"\n🔍 Connection Pool Metrics:")
            print(f"  Manager ID:              {snapshot.get('manager_id', 'N/A')}")
            print(f"  Engine ID:               {snapshot.get('engine_id', 'N/A')}")
            print(f"  Backend:                 {snapshot.get('backend', 'N/A')}")
            print(f"  Pool Class:              {snapshot.get('pool_class', 'N/A')}")

            metrics = snapshot.get('metrics', {})
            if metrics:
                print(f"\n📈 Pool Event Counters:")
                print(f"  Connects:                {metrics.get('connects', 0)}")
                print(f"  Checkouts:               {metrics.get('checkouts', 0)}")
                print(f"  Checkins:                {metrics.get('checkins', 0)}")
                print(f"  Disconnects:             {metrics.get('disconnects', 0)}")
                print(f"  Invalidations:           {metrics.get('invalidate', 0)}")

                # Leak detection based on pool metrics
                checkouts = metrics.get('checkouts', 0)
                checkins = metrics.get('checkins', 0)
                diff = checkouts - checkins

                print(f"\n🔍 Pool Balance:")
                print(f"  Checkouts - Checkins:    {diff}")
                if diff > 0:
                    print(f"  ⚠️  WARNING: {diff} session(s) checked out but not checked in!")
                else:
                    print(f"  ✅ PASS: All sessions properly returned to pool")

        print("="*80 + "\n")

    print("\n" + "="*80)
    print("TEST COMPLETED")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
