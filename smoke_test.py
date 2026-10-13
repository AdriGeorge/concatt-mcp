"""Read-only MCP-to-Concat smoke test. Does not print project data or secrets."""
import asyncio
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    base = Path(__file__).resolve().parent
    async with stdio_client(StdioServerParameters(command=str(base/'start.sh'))) as (r,w):
        async with ClientSession(r,w) as s:
            init = await s.initialize()
            tools = (await s.list_tools()).tools
            assert len(tools)==22, len(tools)
            print('MCP initialization and 22 tool definitions: PASS')
            for name in ('concat_version','concat_project_list','concat_catalogue_list','concat_template_list','concat_events'):
                response = await s.call_tool(name,{})
                assert not response.isError, name
                value = json.loads(response.content[0].text)
                if name == 'concat_version':
                    assert value['concat']=='0.2.6' and value['apiVersion']=='0.2'
                print(name+': PASS')
            events = json.loads((await s.call_tool('concat_events',{})).content[0].text)
            assert events['connection']==1, events
            print('Single persistent authenticated connection: PASS')
            bad = await s.call_tool('concat_project_get',{})
            assert bad.isError
            bad = await s.call_tool('concat_auth',{'token':'not-a-real-token'})
            assert bad.isError
            resources = await s.list_resources()
            await s.read_resource(resources.resources[0].uri)
            print('Argument validation, auth not exposed, command reference: PASS')

if __name__=='__main__': asyncio.run(main())
