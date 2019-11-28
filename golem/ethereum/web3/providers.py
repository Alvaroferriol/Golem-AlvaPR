import logging
import socket

from typing import Optional

from web3.exceptions import CannotHandleRequest
from web3.providers.rpc import HTTPProvider

logger = logging.getLogger(__name__)

RETRIES = 3


class ProviderProxy(HTTPProvider):

    def __init__(self, initial_addr_list) -> None:
        super().__init__()
        self.initial_addr_list = initial_addr_list
        self.addr_list = initial_addr_list
        self.provider = self._create_remote_rpc_provider()

        self._retries = RETRIES
        self._cur_errors = 0

    def make_request(self, method, params):
        import threading
        import time
        ts = time.time()
        thread = threading.current_thread()

        logger.debug('ProviderProxy.make_request(%r, %r) at %s in %r', method, params, ts, thread)
        response = None
        while response is None:
            try:
                response = self.provider.make_request(method, params)
                logger.debug(
                    'GETH %s: request successful %s (%r, %r) -- result = %r',
                    ts,
                    self.provider.endpoint_uri, method, params, response
                )
            except (ConnectionError, ValueError,
                    socket.error, CannotHandleRequest) as exc:
                self._cur_errors += 1
                retry = self._cur_errors < self._retries
                logger.debug(
                    "GETH %s: request failure%s"
                    ". %s (%r, %r), error='%s', "
                    'cur_errors=%s, retries=%s',
                    ts,
                    ', retrying' if retry else '',
                    self.provider.endpoint_uri, method, params, exc,
                    self._cur_errors, self._retries,
                )
                if not retry:
                    self._handle_remote_rpc_provider_failure(method, ts)
                    self.reset()
            except Exception as exc:
                logger.error("Unknown exception %r", exc)
                raise
            else:
                self.reset()
                self.addr_list = self.initial_addr_list

        return response

    def _create_remote_rpc_provider(self):
        addr = self.addr_list.pop(0)
        logger.info('GETH: connecting to remote RPC interface at %s', addr)
        return HTTPProvider(addr)

    def _handle_remote_rpc_provider_failure(self, method, ts):
        if not self.addr_list:
            raise Exception(
                "GETH %s: No more addresses to try, request failed. method='%s'",
                ts,
                method
            )
        logger.warning(
            "GETH %s: '%s' request failed on '%s', "
            "reconnecting to another provider.",
            ts,
            method, self.provider.endpoint_uri,
        )
        self.provider = self._create_remote_rpc_provider()

    def reset(self):
        """ Resets the current error number counter """
        self._cur_errors = 0
