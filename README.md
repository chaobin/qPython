This is a fork of [exxeleron/qPython](https://github.com/exxeleron/qPython)

The motivation behind this fork is to add cooperative async communication for
the qPython library developed by [DEVnet](devnet.de).

Rationale
===

*1. what is async IPC in KDB's terms?*

https://code.kx.com/v2/basics/ipc/#async-message-set would imply that the requesting
process would sensibly choose to not ask for return (_by negating the handle obtained
through hopen_), typically when all is wanted is to cause a state to be changed in
the server, namely the "set" nature in the request.

When the requesting process does so, KDB calls the _.z.ps_ from the two message handlers:

```q
/ by default, the two handlers both points to the function value
.z.pg:value / port get - for sync messages
.z.ps:value / port set - for async messages
```

As a result, an async request in IPC with KDB has the following characteristics:

- the requesting process, will have the
  [messaging thread](http://www.timestored.com/kdb-guides/interprocess-communication)
  to send the request, instead of directly issuing them in the main thread, thus the call
  is not supposed to __wait__
- the receiving end, after processing the request, will not send the result (by default),
  depending on the behaviour of the async message handler, which can very well
  be overriden
- it is implied by KDB that you usually would choose to not wait for your request
  if you are requesting to **set** a state
- but with the above, the attentious user would have realized that by engineering
  a clever async message handler, one could also have the async message handler
  to send the result back in yet another returning async request (e.g., designating
  a function in the calling end to receive all result and dispatch them accordingly)

The above discussion on async IPC in KDB's realm is meant to provide an understanding
ground for when/why we need the requests to be sent asynchronosely.

*2. what happens to the above ability when we are IPC'ing with KDB in a non-KDB system?*

e.g., Python?

- the client's no-wait behaviour has to be mimic'ed
- when sending a message/query to KDB, one could specify the message type, opting for
  either SYNC or ASYNC (a different byte in the requesting header)
- the tricky part is how to receive the result of an async request?
  which  becomes necessary when your request is not merely "setting" sth

*3. what is the most important user case with an async request anyway? what is the value here?*

Imagine a request like this:

```q
select [1000000] from eodPosition
```

A row in the table may have 20 columns. So this is one 1-million by 20 matrix we are requesting.
Suppose the optimized qPython's uncompressing and deserializing algorithm takes 10 seconds to parse
this message, in a contentious networking environment, the IPC also needs an addition of 5 seconds
to deliver the serialized buffer of this matrix from the server's out-bounding buffer to the
client's in-bounding buffer. The elimination of this 5 seconds out of the _system critical path_
is the true value of the async request. So that while that 5 seconds of moving bytes is taking
place, the client is moving along to handle other requests. What is left from here after above
discussion is how we could pick up from the point when that deserialized buffer of the big matrix
is delivered, so we will start to work our CPUs to crunch numbers.

Once the realization becomes clear, the true problem surfaces, which is how best we could eliminate
the waiting, whilst providing __a good API__.


Solution
===

Non-blocking IO, combined with explicit asynchronouse programming paradigm.

The implementation added to the fork of the qPython library is using tornado's IO stream, also
making use of the async/await primitive added by later versions of Python 3. 

__Limitation__

1. The support for older versions of Python are not considered. One could adapt using _gen.coroutine_.
1. There is some although reasonably small duplication of code (But thanks to the excellently
   organized implementation of qPython library, it is made extremely easy for one to address
   the IO handling of the library, without almost none to worry about the bytes assembling. Kudos
   to the author of the library)

Study & Comparison
===

1. https://emptysqua.re/blog/motor-internals-how-i-asynchronized-a-synchronous-library/
1. http://blog.joshhaas.com/2011/06/marrying-boto-to-tornado-greenlets-bring-them-together/
1. https://www.codeproject.com/Articles/70302/Redirecting-functions-in-shared-ELF-libraries
1. https://github.com/douban/greenify

The above are the highlights of my research into the discussed solution. They are quite interesting
and in some cases are magically useful. The central topic here is how to turn a library that
does blocking IO to use non-blocking IO, without having to rewrite much of its API.

Although I did not end up using this approach, they served as a critical counter-point, perhaps
it only does not make enough sense in my use case.

The library greenlet extends CPython with a true coroutine (no, not your primitive goto in C), where
programmer will have complete control over his/her execution flow. The ideas above make use of this
ability, in combination of a surgical patch of the target library's socket handling part of code to
switch to use non-blocking IO, cleverly pauses the execution at the point of IO waiting
(_greenlet.switch()_), then resumes the execution later when the IO becomes ready. The idea
is very clever and has a number of advantages:

- minimal intrusion into target library
- possibly minimal code in implementation also
- possibly applicable to most libraries out there, e.g., pymysql, pymemcache, etc
- possibly will not introduce too much intrusive dependency. e.g., greenlet is a clean
  dependency in the sense that it does not alter the interpreter's behaviour unexpectedly
  and does not stop you from using almost any other library (unlike gevent)

I optted not to use this approach because:

- I do not like the idea that in the end one still has to work around a _callback_ (
  maybe I was not smart enough to figure out how to work around this)
- I do not want my user to have to think of their code as a callback for the most part
- qPython has an excellent code base, that I got away with almost all clean implementation
  by pure subclassing. 


How does it look like
===

```python
from qpython.asyncqconnection import Pool


# obtain a pool of connections
# one needs a pool in here, because we want to be able to
# issue multiple requests to the KDB service. Without a pool,
# when you are waiting for the result requested in one connection,
# that connection can not be used by other requests.
pool = Pool(5, ('air.local', 8888), retry=True)
await pool.init()

async def query(q):
    start = time.time()
    result = await pool.query(q)
    end = time.time()
    print('query took %s secs to finish' % (end - start))
    return result

async def task(delay):
    start = time.time()
    await asyncio.sleep(delay)
    end = time.time()
    print('query took %s secs to finish' % (end - start))

start = time.time()
await asyncio.wait([
    query('system "sleep 2"'),
    task(5), # dominant path
    query('([] a: til 300000; b: 10 + til 300000)'),
    query('til 10'),
    ])
end = time.time()
print('all tasks took %s to finish' % (end - start))
```

With the above, the result shows that the system's critical path takes no more then
what its dominant path takes to finish:

```text
query took 2.140315294265747 secs to finish
query took 2.1406819820404053 secs to finish
query took 3.2969367504119873 secs to finish
query took 5.002039909362793 secs to finish
all tasks took 5.003116846084595 to finish
```

The pool following ability:

1. queue based, eliminating race condition
1. automatically replacing a dead connection with live connection
1. automatically retrying at failed attempts to reconnect. this allows
   the system to self-heal


