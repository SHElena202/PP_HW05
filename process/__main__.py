import sys
import logging
import httpd

from argparse import ArgumentParser
from pathlib import Path



def parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("-r", "--root", default="httptest-suite-master", help="Document root")
    parser.add_argument("-w", "--workers", type=int, default=4, help="Number of workers")
    parser.add_argument("-a", "--address", default="127.0.0.1", help="Server address")
    parser.add_argument("-p", "--port", type=int, default="8080", help="Server port")

    return parser.parse_args()


args = parse_args()

n_workers = args.workers
if n_workers < 0:
    logging.error('Ivalid number of workers')
    sys.exit()

document_root = Path(args.root)
if not document_root.is_dir():
    logging.error('Document root is not a directory')
    sys.exit()

port = args.port
if port < 1:
    logging.error('Ivalid port')
    sys.exit()

httpd.serve_forever(args.address, port, document_root.resolve(), n_workers)
