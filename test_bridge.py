import asyncio
import json
import unittest
from concat_mcp import ConcatClient, BridgeError, RPCError

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections = 0
        self.seen = []
        self.writers = []
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        port = self.server.sockets[0].getsockname()[1]
        self.client = ConcatClient('127.0.0.1', port, 'test-secret', .15)

    async def asyncTearDown(self):
        await self.client.close()
        for writer in self.writers:
            writer.close()
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                req = json.loads(line); method = req['method']; self.seen.append(method)
                if method == 'drop':
                    break
                if method == 'timeout':
                    continue
                if method == 'event':
                    # Multiple frames and fragmented TCP delivery must both work.
                    event = (json.dumps({'jsonrpc':'2.0','method':'export.done','params':{'job':'j1'}})+'\n').encode()
                    writer.write(event[:7]); await writer.drain()
                    writer.write(event[7:])
                result = {'apiVersion':'0.2','concat':'0.2.6'} if method == 'version' else {'ok':True}
                reply = {'jsonrpc':'2.0','id':req['id'],'result':result}
                if method == 'error': reply = {'jsonrpc':'2.0','id':req['id'],'error':{'code':-1,'message':'test-secret'}}
                writer.write((json.dumps(reply)+'\n').encode()); await writer.drain()
        finally:
            writer.close()

    async def test_persistent_concurrent_requests_and_events(self):
        results = await asyncio.gather(*(self.client.call('event',{}) for _ in range(8)))
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(self.connections,1)
        self.assertEqual(self.seen.count('auth'),1)
        self.assertEqual(len(self.client.poll_events()['events']),8)
        self.assertEqual(len(self.client.poll_events(7)['events']),1)

    async def test_disconnect_not_replayed_and_next_call_reauthenticates(self):
        with self.assertRaises(BridgeError): await self.client.call('drop',{})
        await self.client.call('event',{})
        self.assertEqual(self.seen.count('drop'),1)
        self.assertEqual(self.seen.count('auth'),2)
        self.assertEqual(self.client.generation,2)

    async def test_timeout_not_replayed(self):
        with self.assertRaises(BridgeError): await self.client.call('timeout',{})
        await self.client.call('event',{})
        self.assertEqual(self.seen.count('timeout'),1)
        self.assertEqual(self.seen.count('auth'),2)

    async def test_error_redaction_and_connection_retained(self):
        with self.assertRaises(RPCError) as ctx: await self.client.call('error',{})
        self.assertNotIn('test-secret',str(ctx.exception))
        await self.client.call('event',{})
        self.assertEqual(self.connections,1)

    async def test_cancellation_invalidates_connection(self):
        task = asyncio.create_task(self.client.call('timeout',{}))
        while 'timeout' not in self.seen: await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        await self.client.call('event',{})
        self.assertEqual(self.connections,2)

    async def test_event_buffer_loss_reported(self):
        self.client.events.extend({'sequence':i} for i in range(1,1002))
        self.client.event_sequence=1001
        self.assertEqual(self.client.poll_events()['dropped'],1)

if __name__=='__main__': unittest.main()
