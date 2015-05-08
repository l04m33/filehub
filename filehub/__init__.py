import asyncio
import logging
import argparse
import pkg_resources
import pyx
from pyx.log import logger
from .resources import RootResource


__all__ = ['main', '__version__']


__version__ = '0.1.1'


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--bind',
                        help='Specify bind address (default: all interfaces)',
                        default='',
                        type=str)
    parser.add_argument('-p', '--port',
                        help='Which port to listen (default: 8000)',
                        default=8000,
                        type=int)
    parser.add_argument('--backlog',
                        help='Backlog for the listening socket (default: 128)',
                        default=128,
                        type=int)
    parser.add_argument('--loglevel',
                        help='Log level (default: info)',
                        default='info',
                        type=str,
                        choices=[
                            'critical', 'fatal', 'error',
                            'warning', 'info', 'debug',
                        ])

    return parser.parse_args()


def main():
    args = parse_arguments()

    logging.basicConfig(level=args.loglevel.upper())

    loop = asyncio.get_event_loop()

    pkg_provider = pkg_resources.get_provider(__package__)
    res_mngr = pkg_resources.ResourceManager()
    ui_page = pkg_provider.get_resource_string(res_mngr, 'ui.html')

    def root_factory(req):
        return RootResource(ui_page)

    req_cb = pyx.HttpRequestCB(root_factory)
    conn_cb = pyx.HttpConnectionCB(req_cb)

    starter = asyncio.start_server(conn_cb, args.bind, args.port,
                                   backlog=args.backlog,
                                   reuse_address=True,
                                   loop=loop)
    server = loop.run_until_complete(starter)

    if args.bind == '':
        logger('filehub').info(
            'Server serving at <all interfaces>:{}'.format(args.port))
    else:
        logger('filehub').info(
            'Server serving at {}:{}'.format(args.bind, args.port))

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()
