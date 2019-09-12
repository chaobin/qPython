import struct
import sys
if sys.version > '3':
    from sys import intern
    unicode = str

import numpy

try:
    from qpython.fastutils import uncompress
except:
    from qpython.utils import uncompress
from qpython import MetaData, CONVERSION_OPTIONS
from qpython.qreader import QReader, QMessage, QReaderException


__all__ = ['AsyncQReader']


class AsyncQReader(QReader):

    async def read(self, source = None, **options):
        message = await self.read_header(source)
        message.data = await self.read_data(message.size, message.is_compressed, **options)

        return message

    async def read_header(self, source = None):
        if self._stream:
            header = await self._read_bytes(8)
            self._buffer.wrap(header)
        else:
            self._buffer.wrap(source)

        self._buffer.endianness = '<' if self._buffer.get_byte() == 1 else '>'
        self._is_native = self._buffer.endianness == ('<' if sys.byteorder == 'little' else '>')
        message_type = self._buffer.get_byte()
        message_compressed = self._buffer.get_byte() == 1
        # skip 1 byte
        self._buffer.skip()

        message_size = self._buffer.get_int()
        return QMessage(None, message_type, message_size, message_compressed)

    async def read_data(self, message_size, is_compressed = False, **options):
        self._options = MetaData(**CONVERSION_OPTIONS.union_dict(**options))

        if is_compressed:
            if self._stream:
                self._buffer.wrap(await self._read_bytes(4))
            uncompressed_size = -8 + self._buffer.get_int()
            compressed_data = (await self._read_bytes(message_size - 12)) \
            	if self._stream else self._buffer.raw(message_size - 12)

            raw_data = numpy.fromstring(compressed_data, dtype = numpy.uint8)
            if  uncompressed_size <= 0:
                raise QReaderException('Error while data decompression.')

            raw_data = uncompress(raw_data, numpy.intc(uncompressed_size))
            raw_data = numpy.ndarray.tostring(raw_data)
            self._buffer.wrap(raw_data)
        elif self._stream:
            raw_data = await self._read_bytes(message_size - 8)
            self._buffer.wrap(raw_data)
        if not self._stream and self._options.raw:
            raw_data = self._buffer.raw(message_size - 8)

        return raw_data if self._options.raw else self._read_object()

    async def _read_bytes(self, length):
        if not self._stream:
            raise QReaderException('There is no input data. QReader requires either stream or data chunk')

        if length == 0:
            return b''
        else:
            data = await self._stream.read_bytes(length)

        if len(data) == 0:
            raise QReaderException('Error while reading data')
        return data
