import struct
try:
    from cStringIO import BytesIO
except ImportError:
    from io import BytesIO

from qpython import MetaData, CONVERSION_OPTIONS
from qpython.qtype import *  # @UnusedWildImport
from qpython.qwriter import QWriter, QWriterException, ENDIANESS


__all__ = ['QWriter']


class AsyncQWriter(QWriter):

    async def write(self, data, msg_type, **options):
        '''Serializes and pushes single data object to a wrapped stream.
        
        :Parameters:
         - `data` - data to be serialized
         - `msg_type` (one of the constants defined in :class:`.MessageType`) -
           type of the message
        :Options:
         - `single_char_strings` (`boolean`) - if ``True`` single char Python 
           strings are encoded as q strings instead of chars, 
           **Default**: ``False``
        
        :returns: if wraped stream is ``None`` serialized data, 
                  otherwise ``None`` 
        '''
        self._buffer = BytesIO()

        self._options = MetaData(**CONVERSION_OPTIONS.union_dict(**options))

        # header and placeholder for message size
        self._buffer.write(('%s%s\0\0\0\0\0\0' % (ENDIANESS, chr(msg_type))).encode(self._encoding))

        self._write(data)

        # update message size
        data_size = self._buffer.tell()
        self._buffer.seek(4)
        self._buffer.write(struct.pack('i', data_size))

        # write data to socket
        if self._stream:
            await self._stream.write(self._buffer.getvalue())
        else:
            return self._buffer.getvalue()