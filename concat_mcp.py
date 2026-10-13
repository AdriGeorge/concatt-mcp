"""Local stdio MCP bridge for the verified Concat 0.2.6 / API 0.2 contract."""
import asyncio
from collections import deque
from contextlib import suppress
import ipaddress
import json
import os
from pathlib import Path

from dotenv import dotenv_values
import jsonschema
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

BASE = Path(__file__).resolve().parent

class BridgeError(Exception):
    pass

class RPCError(BridgeError):
    def __init__(self, error):
        self.error = error
        super().__init__(json.dumps(error, ensure_ascii=False))

class ConcatClient:
    def __init__(self, host, port, token, timeout=30):
        if host != 'localhost' and not ipaddress.ip_address(host).is_loopback:
            raise BridgeError('Concat must use a loopback address.')
        if not token:
            raise BridgeError('Missing CONCAT_API_TOKEN in the project .env file.')
        self.host, self.port, self.token, self.timeout = host, port, token, timeout
        self.lock = asyncio.Lock()
        self.writer = self.reader_task = None
        self.pending = {}
        self.next_id = 0
        self.generation = 0
        self.events = deque(maxlen=1000)
        self.event_sequence = 0

    def redact(self, value):
        # Redact both plain and JSON-escaped spellings, including server errors.
        return value.replace(self.token, '[REDACTED]').replace(json.dumps(self.token)[1:-1], '[REDACTED]')

    async def close(self):
        task, writer = self.reader_task, self.writer
        self.reader_task = self.writer = None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if writer:
            writer.close()
            with suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 2)

    async def _read(self, reader):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    raise BridgeError('Concat disconnected; session state may have been lost.')
                message = json.loads(line)
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise BridgeError('Invalid JSON-RPC envelope from Concat.')
                if 'id' not in message and isinstance(message.get('method'), str):
                    self.event_sequence += 1
                    self.events.append({'sequence': self.event_sequence, 'connection': self.generation, **message})
                elif 'id' in message:
                    future = self.pending.get(message['id'])
                    if future is not None and not future.done():
                        future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(BridgeError('Concat connection lost or invalid response. The request was not retried; its outcome may be unknown. Reopen projects after reconnecting.'))
        finally:
            if self.writer:
                self.writer.close()

    async def _exchange(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            payload = json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}, allow_nan=False)
            async with asyncio.timeout(self.timeout):
                self.writer.write((payload + '\n').encode())
                await self.writer.drain()
                message = await future
            if 'error' in message:
                raise RPCError(message['error'])
            if 'result' not in message:
                raise BridgeError('Concat response has no result.')
            return message['result']
        finally:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _connect(self):
        if self.writer and not self.writer.is_closing() and self.reader_task and not self.reader_task.done():
            return
        await self.close()
        reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port, limit=64 * 1024 * 1024), 5)
        self.generation += 1
        self.reader_task = asyncio.create_task(self._read(reader))
        try:
            await self._exchange('auth', {'token': self.token})
        except RPCError:
            raise BridgeError('Concat authentication failed. Check the token locally in .env.') from None
        version = await self._exchange('version', {})
        if version.get('apiVersion') != '0.2' or version.get('concat') != '0.2.6':
            raise BridgeError('This bridge was validated for Concat 0.2.6 / API 0.2. Revalidate the API before using another version.')

    async def call(self, method, params):
        async with self.lock:
            try:
                await self._connect()
                return await self._exchange(method, params)
            except RPCError as exc:
                raise RPCError(json.loads(self.redact(json.dumps(exc.error)))) from None
            except BaseException as exc:
                await self.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, BridgeError):
                    raise
                raise BridgeError('Concat is unavailable or the request timed out. No request was retried. Check Settings > Remote; inspect project state before repeating an edit.') from None

    def poll_events(self, after=0):
        oldest = self.events[0]['sequence'] if self.events else self.event_sequence + 1
        return {'events': [e for e in self.events if e['sequence'] > after], 'cursor': self.event_sequence,
                'dropped': max(0, oldest - after - 1), 'connection': self.generation,
                'connected': bool(self.writer and not self.writer.is_closing()),
                'note': 'Events are retained only for this MCP process. A changed connection number means projects may need reopening.'}

S = {'type': 'string', 'minLength': 1}
B = {'type': 'boolean'}
N = {'type': 'number', 'minimum': 0}
I = {'type': 'integer', 'minimum': 1, 'maximum': 8192}
def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}
VIDEO = obj({'width': I, 'height': I, 'rateNum': {'type':'integer','minimum':1}, 'rateDen': {'type':'integer','minimum':1}}, ('width','height','rateNum','rateDen'))
REGISTRY = {}
def register(method, description, properties=None, required=(), read=False):
    REGISTRY['concat_' + method.replace('.', '_')] = (method, types.Tool(
        name='concat_' + method.replace('.', '_'), description=description,
        inputSchema=obj(properties or {}, required),
        annotations=types.ToolAnnotations(readOnlyHint=read, destructiveHint=not read, idempotentHint=read, openWorldHint=False)))

register('version', 'Get verified Concat version and capabilities.', read=True)
register('project.list', 'List recent projects (not necessarily open).', read=True)
register('project.create', 'Create and open a new project under a permitted write root.', {'location':S,'name':S,'video':VIDEO}, ('location','name'))
register('project.open', 'Open a project folder in this API session. Required before accessing it.', {'path':S}, ('path',))
register('project.close', 'Close project. save is required explicitly: false discards unsaved edits.', {'path':S,'save':B}, ('path','save'))
register('project.get', 'Read an open project and its editing state.', {'path':S}, ('path',), True)
register('project.document', 'Read the document an explicit save would write.', {'path':S}, ('path',), True)
register('project.save', 'Save an open project, optionally changing its name.', {'path':S,'name':S}, ('path',))
register('project.setVideo', 'Change timeline dimensions and frame rate (undoable).', {'path':S,'video':VIDEO}, ('path','video'))
register('edit.apply', 'Apply a Concat edit command (undoable). Consult concat://edit-commands before constructing commands. Read current state first.', {'path':S,'command':{'type':'object','properties':{'op':S},'required':['op']}}, ('path','command'))
for method in ('edit.undo','edit.redo'):
    register(method, 'Step the open project editing history '+('backward.' if method.endswith('undo') else 'forward.'), {'path':S}, ('path',))
register('media.probe', 'Inspect a local media file without changing a project.', {'path':S}, ('path',), True)
register('media.import', 'Import a local media file into an open project.', {'path':S,'file':S}, ('path','file'))
register('catalogue.list', 'List installed effects and their parameter definitions.', {'kind':{'enum':['effect','filter','audio','transition','generator']}}, read=True)
register('template.list', 'List available templates.', read=True)
register('template.instantiate', 'Create and open a project from a template, filling every media slot.', {'template':S,'location':S,'name':S,'fills':{'type':'array','items':obj({'mediaId':S,'file':S},('mediaId','file'))}}, ('template','location','name','fills'))
register('template.save', 'Save an open project as a new template.', {'path':S,'name':S}, ('path','name'))
export_props={'path':S,'output':S,'crf':{'type':'integer','minimum':0,'maximum':63},'preset':{'enum':['ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow']},'width':I,'height':I,'rateNum':{'type':'integer','minimum':1},'rateDen':{'type':'integer','minimum':1},'codec':{'enum':['h264','hevc','av1']},'tenBit':B,'colorRange':{'enum':['limited','full']},'hdr':B}
register('export.run', 'Start an export job to a permitted output path. Poll concat_events for completion; starting a job does not mean it finished.', export_props, ('path','output'))
register('export.cancel', 'Cancel a running export job.', {'job':S}, ('job',))
register('preview.frame', 'Render a PNG preview, inline or to a permitted output path. Width and height must be supplied together.', {'path':S,'time':N,'output':S,'width':I,'height':I}, ('path','time'))
EVENT_TOOL=types.Tool(name='concat_events',description='Read buffered Concat progress/completion events after a cursor. Returns dropped count if the 1000-event buffer overflowed.',inputSchema=obj({'after':{'type':'integer','minimum':0}}),annotations=types.ToolAnnotations(readOnlyHint=True,openWorldHint=False))

async def serve():
    config = {**dotenv_values(BASE / '.env'), **os.environ}
    client = ConcatClient(config.get('CONCAT_API_HOST','127.0.0.1'),int(config.get('CONCAT_API_PORT','7420')),config.get('CONCAT_API_TOKEN',''))
    server = Server('concat', version='1.0.0', instructions='Local Concat 0.2.6 bridge. Open projects explicitly. Inspect state before edits, save deliberately, and poll events after exports. Never repeat an edit automatically after a timeout or disconnect: its outcome is unknown. Only use validated tools; auth is internal.')

    @server.list_tools()
    async def list_tools():
        return [entry[1] for entry in REGISTRY.values()] + [EVENT_TOOL]

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri='concat://edit-commands',name='Concat edit command reference',mimeType='text/plain')]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) != 'concat://edit-commands':
            raise ValueError('Unknown resource')
        return (BASE / 'edit-commands.md').read_text()

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            tool = EVENT_TOOL if name == 'concat_events' else REGISTRY[name][1]
            try:
                jsonschema.validate(arguments, tool.inputSchema)
            except jsonschema.ValidationError:
                raise BridgeError('Invalid arguments. Follow the tool input schema.') from None
            if name == 'concat_events':
                result = client.poll_events(**arguments)
            else:
                method = REGISTRY[name][0]
                if method == 'preview.frame' and (('width' in arguments) != ('height' in arguments)):
                    raise BridgeError('Supply both width and height, or neither.')
                result = await client.call(method, arguments)
            return types.CallToolResult(content=[types.TextContent(type='text',text=client.redact(json.dumps(result,ensure_ascii=False,allow_nan=False)))])
        except KeyError:
            return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text='Unknown Concat tool.')])
        except BridgeError as exc:
            return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text=client.redact(str(exc)))])
        except Exception:
            return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text='Bridge failed safely; inspect local configuration. No request was retried.')])
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await client.close()

if __name__ == '__main__':
    try:
        asyncio.run(serve())
    except (Exception, KeyboardInterrupt):
        # Do not print exception payloads or configuration to stdio/stderr.
        import sys
        print('Concat bridge stopped. Check local configuration and dependencies.',file=sys.stderr)
        sys.exit(1)
