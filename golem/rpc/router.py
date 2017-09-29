import asyncio
import logging
import multiprocessing
import os
import queue
import time
from collections import namedtuple

import txaio

from golem.core.async import async_queue_threadsafe

txaio.use_twisted = lambda: None

from crossbar.common import checkconfig
from multiprocessing import Process

from golem.core.common import is_windows, is_osx
from golem.rpc.session import WebSocketAddress

logger = logging.getLogger('golem.rpc.crossbar')


CrossbarRouterOptions = namedtuple(
    'CrossbarRouterOptions',
    ['cbdir', 'logdir', 'loglevel', 'argv', 'config']
)


def _start_router(options, node_config, queue):
    reactor = _install_reactor()

    # Patch txaio with multiprocessing
    import txaio
    from txaio import tx

    txaio._explicit_framework = 'twisted'
    txaio._use_framework(tx)
    txaio.using_twisted = True
    txaio.using_asyncio = False

    # Import node
    from crossbar.controller.node import Node
    from crossbar.controller.node import default_native_workers

    try:

        workers = default_native_workers()
        node = Node(options.cbdir)
        node.maybe_generate_key(options.cbdir)

        checkconfig.check_config(node_config, workers)

        node._config = node_config
        start_result = node.start()
        start_result.addBoth(queue.put)

    except Exception as exc:
        queue.put(exc)

    reactor.run()


def _install_reactor():
    if is_osx():
        from twisted.internet import kqreactor
        kqreactor.install()
    elif is_windows():
        from twisted.internet import iocpreactor
        iocpreactor.install()

    from twisted.internet import reactor
    return reactor


class CrossbarRouter(object):

    serializers = ['msgpack']

    def __init__(self, host='localhost', port=61000, realm='golem',
                 datadir=None, crossbar_dir='crossbar', crossbar_log_level='trace'):
        if datadir:
            self.working_dir = os.path.join(datadir, crossbar_dir)
        else:
            self.working_dir = crossbar_dir

        if not os.path.exists(self.working_dir):
            os.makedirs(self.working_dir)

        if not os.path.isdir(self.working_dir):
            raise IOError("'{}' is not a directory".format(self.working_dir))

        self.address = WebSocketAddress(host, port, realm)
        self.log_level = crossbar_log_level
        self.node = None
        self.pubkey = None

        self.options = self._build_options()
        self.config = self._build_config(self.address, self.serializers)

        self._queue = multiprocessing.Queue()
        self.router_proc = multiprocessing.Process(target=_start_router,
                                                   args=(self.options,
                                                         self.config,
                                                         self._queue))

        logger.debug('xbar init with cfg: %s', self.config)

    def start(self, callback, errback):

        async def queue_wait(timeout=30):
            call = async_queue_threadsafe
            deadline = time.time() + timeout

            while True:
                try:
                    result = self._queue.get(block=False)
                except queue.Empty:
                    await asyncio.sleep(0.25)
                    if time.time() > deadline:
                        call(errback, TimeoutError('Router startup timeout'))
                else:
                    if isinstance(result, Exception):
                        call(errback, result)
                    return call(callback, result)

        logger.info('Starting Crossbar router ...')

        self.router_proc.start()
        asyncio.ensure_future(queue_wait())

    def stop(self):
        if self.router_proc:
            self.router_proc.terminate()
            self.router_proc.join()

    def _build_options(self, argv=None, config=None):
        return CrossbarRouterOptions(
            cbdir=self.working_dir,
            logdir=None,
            loglevel=self.log_level,
            argv=argv,
            config=config
        )

    @staticmethod
    def _build_config(address, serializers, allowed_origins='*', realm='golem',
                      enable_webstatus=False):

        return {
            'version': 2,
            'workers': [{
                'type': 'router',
                'options': {
                    'title': 'Golem'
                },
                'transports': [
                    {
                        'type': 'websocket',
                        'serializers': serializers,
                        'endpoint': {
                            'type': 'tcp',
                            'interface': str(address.host),
                            'port': address.port
                        },
                        'url': str(address),
                        'options': {
                            'allowed_origins': allowed_origins,
                            'enable_webstatus': enable_webstatus,
                        }
                    }
                ],
                'components': [],
                "realms": [{
                    "name": realm,
                    "roles": [{
                        "name": 'anonymous',
                        "permissions": [{
                            "uri": '*',
                            "allow": {
                                "call": True,
                                "register": True,
                                "publish": True,
                                "subscribe": True
                            }
                        }]
                    }]
                }],
            }]
        }
