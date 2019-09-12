import socket
import struct
import asyncio
import time

from tornado import ioloop
import tornado.iostream
from tornado.queues import Queue

from qpython.qconnection import (
    QConnectionException,
    QAuthenticationException,
    MessageType,
    QConnection
    )
from qpython import MetaData, CONVERSION_OPTIONS
from qpython.qtype import QException
from qpython.asyncqreader import AsyncQReader, QReaderException
from qpython.asyncqwriter import AsyncQWriter, QWriterException


__all__ = ['AsyncQConnection', 'Pool']


try:
    from qpython._pandas import PandasQReader, PandasQWriter
    class DefaultReader(PandasQReader, AsyncQReader): pass
    class DefaultWriter(PandasQWriter, AsyncQWriter): pass
    PANDAS_AVAILABLE = True
except ImportError:
    DefaultReader = AsyncQReader
    DefaultWriter = AsyncQWriter
    PANDAS_AVAILABLE = False


class AsyncQConnection(QConnection):

    async def open(self):
        if not self._connection:
            if not self.host:
                raise QConnectionException('Host cannot be None')

            await self._init_socket()
            await self._initialize()

            self._writer = self._writer_class(
                self._connection, protocol_version=self._protocol_version, encoding = self._encoding)
            self._reader = self._reader_class(self._connection_file, encoding = self._encoding)

    async def _init_socket(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0) # non-blocking
            s.settimeout(self.timeout)
            self._connection = tornado.iostream.IOStream(s)
            self._connection_file = self._connection
            await self._connection.connect((self.host, self.port))
        except:
            self._connection = None
            self._connection_file = None
            raise

    async def _initialize(self):
        credentials = (self.username if self.username else '') + ':' + (self.password if self.password else '')
        credentials = credentials.encode(self._encoding)
        await self._connection.write(credentials + b'\3\0')
        response = await self._connection.read_bytes(1)

        if len(response) != 1:
            self.close()
            await self._init_socket()

            await self._connection.write(credentials + b'\0')
            response = self._connection.read_bytes(1)
            if len(response) != 1:
                self.close()
                raise QAuthenticationException('Connection denied.')

        self._protocol_version = min(struct.unpack('B', response)[0], 3)

    async def query(self, query, *parameters, **options):
        if not self._connection:
            raise QConnectionException('Connection is not established.')

        if parameters and len(parameters) > 8:
            raise QWriterException('Too many parameters.')

        if not parameters or len(parameters) == 0:
            await self._writer.write(query, MessageType.SYNC, **self._options.union_dict(**options))
        else:
            await self._writer.write([query] + list(parameters), MessageType.SYNC, **self._options.union_dict(**options))

        response = await self.receive(data_only = False, **options)

        if response.type == MessageType.RESPONSE:
            return response.data
        else:
            await self._writer.write(QException('nyi: qPython expected response message'), MessageType.ASYNC if response.type == MessageType.ASYNC else MessageType.RESPONSE)
            raise QReaderException('Received message of type: %s where response was expected')

    async def receive(self, data_only = True, **options):
        result = await self._reader.read(**self._options.union_dict(**options))
        return result.data if data_only else result

class Pool(object):
    
    def __init__(self, maxsize, address, retry=False, max_retries=10, retry_wait=6):
        self.maxsize = maxsize
        self._queue = Queue(maxsize=maxsize)
        self.address = address
        self.retry = retry
        self.max_retries = float('inf') if max_retries is None else max_retries
        self.retry_wait = retry_wait

    def empty(self):
        return self._queue.qsize == 0

    async def init(self):
        for i in range(self.maxsize):
            await self.add()

    async def _create_connection(self):
        host, port = self.address
        connection = AsyncQConnection(host, port,
            pandas=PANDAS_AVAILABLE,
            reader_class=DefaultReader,
            writer_class=DefaultWriter)
        trials = self.max_retries
        while trials > 0:
            try:
                await connection.open()
                break
            except (tornado.iostream.StreamClosedError,) as e:
                if not self.retry: raise e
                if trials == 0: raise e
                await asyncio.sleep(self.retry_wait) # retrying
                trials -= 1
        return connection

    async def add(self):
        conn = await self._create_connection()
        await self._queue.put(conn)

    def schedule_to_reconnect(self):
        loop = ioloop.IOLoop.current()
        loop.add_callback(self.add)

    async def get(self, seconds=2):
        connection = await self._queue.get(timeout=time.time() + seconds)
        return connection

    async def query(self, query):
        # create connection on following conditions
        # - pool is empty
        # - current dequeued connection becomes dead
        connection = await self.get() # connection < pool
        try:
            result = await connection.query(query)
            await self._queue.put(connection) # connection > pool
            return result
        except (tornado.iostream.StreamClosedError,) as e:
            self.schedule_to_reconnect()
            raise e
            

