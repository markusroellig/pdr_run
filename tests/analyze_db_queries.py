#!/usr/bin/env python
"""
Analyze MySQL general query log to quantify database operations.

This script parses the MySQL general log to count:
- Total queries executed
- Read operations (SELECT)
- Write operations (INSERT, UPDATE, DELETE)
- Connection events
- Query types breakdown
"""

import re
import sys
from collections import defaultdict, Counter
from datetime import datetime

def parse_mysql_general_log(log_file):
    """Parse MySQL general query log and extract statistics."""

    stats = {
        'total_queries': 0,
        'connections': 0,
        'disconnections': 0,
        'selects': 0,
        'inserts': 0,
        'updates': 0,
        'deletes': 0,
        'commits': 0,
        'show_queries': 0,
        'other': 0,
        'query_types': Counter(),
        'tables_accessed': Counter(),
    }

    # Parse log file
    with open(log_file, 'r') as f:
        for line in f:
            # Connection events
            if 'Connect' in line:
                stats['connections'] += 1
            elif 'Quit' in line or 'Disconnect' in line:
                stats['disconnections'] += 1

            # Query events
            elif 'Query' in line:
                stats['total_queries'] += 1

                # Extract the SQL statement
                parts = line.split('\tQuery\t')
                if len(parts) > 1:
                    sql = parts[1].strip()

                    # Classify query type
                    sql_upper = sql.upper()

                    if sql_upper.startswith('SELECT'):
                        stats['selects'] += 1
                        stats['query_types']['SELECT'] += 1
                        # Extract table names from SELECT
                        match = re.search(r'FROM\s+(\w+)', sql_upper)
                        if match:
                            stats['tables_accessed'][match.group(1)] += 1

                    elif sql_upper.startswith('INSERT'):
                        stats['inserts'] += 1
                        stats['query_types']['INSERT'] += 1
                        # Extract table names from INSERT
                        match = re.search(r'INSERT\s+INTO\s+(\w+)', sql_upper)
                        if match:
                            stats['tables_accessed'][match.group(1)] += 1

                    elif sql_upper.startswith('UPDATE'):
                        stats['updates'] += 1
                        stats['query_types']['UPDATE'] += 1
                        # Extract table names from UPDATE
                        match = re.search(r'UPDATE\s+(\w+)', sql_upper)
                        if match:
                            stats['tables_accessed'][match.group(1)] += 1

                    elif sql_upper.startswith('DELETE'):
                        stats['deletes'] += 1
                        stats['query_types']['DELETE'] += 1
                        # Extract table names from DELETE
                        match = re.search(r'FROM\s+(\w+)', sql_upper)
                        if match:
                            stats['tables_accessed'][match.group(1)] += 1

                    elif sql_upper.startswith('COMMIT'):
                        stats['commits'] += 1
                        stats['query_types']['COMMIT'] += 1

                    elif sql_upper.startswith('SHOW'):
                        stats['show_queries'] += 1
                        stats['query_types']['SHOW'] += 1

                    else:
                        stats['other'] += 1
                        # Get first word as query type
                        first_word = sql_upper.split()[0] if sql_upper else 'UNKNOWN'
                        stats['query_types'][first_word] += 1

    return stats


def print_statistics(stats):
    """Print formatted statistics."""

    print("\n" + "="*80)
    print("DATABASE QUERY ANALYSIS")
    print("="*80 + "\n")

    # Connection statistics
    print("📡 Connection Events:")
    print(f"  Total Connections:       {stats['connections']}")
    print(f"  Total Disconnections:    {stats['disconnections']}")
    print(f"  Net Connections:         {stats['connections'] - stats['disconnections']}")

    # Query statistics
    print(f"\n📊 Query Statistics:")
    print(f"  Total Queries:           {stats['total_queries']}")

    # Read/Write breakdown
    total_read = stats['selects'] + stats['show_queries']
    total_write = stats['inserts'] + stats['updates'] + stats['deletes']

    print(f"\n📖 Read Operations:        {total_read}")
    print(f"  SELECT queries:          {stats['selects']}")
    print(f"  SHOW queries:            {stats['show_queries']}")

    print(f"\n✍️  Write Operations:       {total_write}")
    print(f"  INSERT queries:          {stats['inserts']}")
    print(f"  UPDATE queries:          {stats['updates']}")
    print(f"  DELETE queries:          {stats['deletes']}")

    print(f"\n💾 Transaction Control:")
    print(f"  COMMIT queries:          {stats['commits']}")

    print(f"\n🔧 Other Operations:       {stats['other']}")

    # Query type breakdown
    print(f"\n📋 Query Type Breakdown:")
    for query_type, count in stats['query_types'].most_common():
        percentage = (count / stats['total_queries'] * 100) if stats['total_queries'] > 0 else 0
        print(f"  {query_type:<20} {count:>6} ({percentage:>5.1f}%)")

    # Table access patterns
    if stats['tables_accessed']:
        print(f"\n🗄️  Most Accessed Tables:")
        for table, count in stats['tables_accessed'].most_common(10):
            print(f"  {table:<30} {count:>6} accesses")

    # Read/Write ratio
    if total_read + total_write > 0:
        read_ratio = (total_read / (total_read + total_write)) * 100
        write_ratio = (total_write / (total_read + total_write)) * 100
        print(f"\n⚖️  Read/Write Ratio:")
        print(f"  Reads:  {read_ratio:>5.1f}%")
        print(f"  Writes: {write_ratio:>5.1f}%")

    print("\n" + "="*80 + "\n")


def main():
    # Enable general log if not already enabled
    print("Enabling MySQL general query log...")
    import subprocess

    # Enable general log
    subprocess.run([
        'mysql', '-h', '127.0.0.1', '-P', '3306', '-u', 'pdr_user', '-ppdr_password',
        '-e', "SET GLOBAL general_log = 'ON'; SET GLOBAL log_output = 'FILE';"
    ], capture_output=True)

    # Get log file location
    result = subprocess.run([
        'mysql', '-h', '127.0.0.1', '-P', '3306', '-u', 'pdr_user', '-ppdr_password',
        '-e', "SHOW VARIABLES LIKE 'general_log_file';"
    ], capture_output=True, text=True)

    print(result.stdout)

    # For Docker, we'll need to access the log from inside the container
    log_file = '/var/lib/mysql/general.log'

    print(f"\n⚠️  Note: General log is inside Docker container")
    print(f"   Log file: {log_file}")
    print(f"\nTo analyze queries:")
    print(f"   1. docker exec pdr_mysql cat {log_file} > /tmp/mysql_queries.log")
    print(f"   2. python {__file__} /tmp/mysql_queries.log")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        log_file = sys.argv[1]
        print(f"Analyzing log file: {log_file}")
        stats = parse_mysql_general_log(log_file)
        print_statistics(stats)
    else:
        main()
