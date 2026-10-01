"""Console-script entry point (``pdr_run``).

Dispatches ``pdr_run status ...`` to the read-only status collector *before*
``pdr_run.cli.runner`` is imported: importing the runner configures logging and
creates the ``logs/`` directory, which a read-only status call must not do.
Everything else goes to the run CLI unchanged.
"""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'status':
        from pdr_run.cli.status import main as status_main
        sys.exit(status_main(sys.argv[2:]))
    from pdr_run.cli.runner import main as runner_main
    return runner_main()


if __name__ == '__main__':
    main()
