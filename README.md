# Concat MCP

A local Model Context Protocol (MCP) server that lets an AI assistant interact with the Concat video editor. Inspect projects, import media, apply timeline edits, render previews, and start exports through Concat's authenticated Remote API.

```text
MCP client → stdio → Concat MCP → local TCP / JSON-RPC → Concat
```

The bridge exposes **22 tools**: 21 Concat API methods and one event polling tool. Concat must be installed and running separately.

## Compatibility

| Component | Requirement |
| --- | --- |
| Concat | Exactly **0.2.6**, with API **0.2** |
| Python | **3.11+**; previously validated with Python 3.12 |
| MCP SDK | **1.30.0**, pinned in `requirements.txt` |
| MCP client | Support for local stdio servers |

The bridge checks both Concat and API versions and rejects other versions. Updating Concat requires revalidating the API contract.

The shell launcher and commands below target macOS/Linux. On Windows, configure your client to launch `.venv\Scripts\python.exe` with the absolute path to `concat_mcp.py` instead.

## Quick start

### 1. Install dependencies

Clone or download this repository, then run from its directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

If you already have a `.env`, keep it and add any missing settings instead of copying over it.

### 2. Configure Concat

Open Concat and enable **Settings → Remote**. Set the token from Concat in your local `.env` file, and match its host and port:

```dotenv
CONCAT_API_HOST=127.0.0.1
CONCAT_API_PORT=7420
CONCAT_API_TOKEN=replace-with-your-concat-remote-token
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONCAT_API_HOST` | `127.0.0.1` | Loopback IP address or `localhost` |
| `CONCAT_API_PORT` | `7420` | Concat Remote API TCP port |
| `CONCAT_API_TOKEN` | None; required | Remote API authentication token |

The bridge loads `.env` beside `concat_mcp.py`, regardless of the client's working directory. Environment variables override `.env`. Only loopback connections are accepted.

Keep your real token in `.env` or the process environment. `.gitignore` excludes `.env`, local environment variants, and the virtual environment; `.env.example` contains placeholders only.

### 3. Connect an MCP client

Add a local **stdio** server with this executable, replacing the path with your checkout's absolute path:

```text
/absolute/path/to/concat-mcp/start.sh
```

No arguments are needed. The launcher uses the repository's virtual environment. Let the MCP client start the process; there is no separate bridge daemon to run.

For clients that use an `mcpServers` JSON configuration:

```json
{
  "mcpServers": {
    "concat": {
      "command": "/absolute/path/to/concat-mcp/start.sh"
    }
  }
}
```

The token does not need to appear in the client's configuration. Restart or reconnect the MCP server after changing `.env`.

### 4. Try it

With Concat running and the MCP server connected, ask your assistant:

> Use Concat to check its version and list my recent projects.

Then try a project-specific request:

> Open the Concat project at `/absolute/path/to/project` and describe its timelines, tracks, and media.

## Available tools

| Area | Tools |
| --- | --- |
| Version | `concat_version` |
| Projects | `concat_project_list`, `concat_project_create`, `concat_project_open`, `concat_project_close`, `concat_project_get`, `concat_project_document`, `concat_project_save`, `concat_project_setVideo` |
| Editing | `concat_edit_apply`, `concat_edit_undo`, `concat_edit_redo` |
| Media | `concat_media_probe`, `concat_media_import` |
| Effects | `concat_catalogue_list` |
| Templates | `concat_template_list`, `concat_template_instantiate`, `concat_template_save` |
| Export | `concat_export_run`, `concat_export_cancel` |
| Preview | `concat_preview_frame` |
| Events | `concat_events` |

Tools expose their argument schemas through MCP. Authentication happens internally and is not exposed as a tool.

The resource **`concat://edit-commands`** serves the bundled [edit command reference](edit-commands.md), containing upstream Rust command definitions and comments for the supported version. Edit commands use an `op` discriminator and camelCase fields. Consult this resource before constructing `concat_edit_apply` arguments.

## Working with projects

1. List recent projects or use a known project folder path.
2. Call `concat_project_open` before accessing an existing project in the API session.
3. Read its state with `concat_project_get` before editing, using the returned IDs for media, tracks, and clips.
4. Apply edits, inspect the result, and save deliberately with `concat_project_save`.
5. After `concat_export_run`, poll `concat_events` for completion or failure. Starting an export does not mean it has finished.

Pass the previous event response's `cursor` as `after` on the next poll. Check `dropped` for events lost to buffer overflow and `connection` for reconnects.

`concat_project_close` requires an explicit `save` boolean: **`false` discards unsaved edits**. For previews, supply both `width` and `height`, or omit both.

Concat's permitted write roots still apply to project creation, previews, and exports. API project sessions may differ from the project open in the GUI; explicitly open the project through the API and respect any server refusal.

## Reliability and limitations

- One persistent authenticated connection per MCP process, with serialized calls and unique request IDs.
- A continuous reader handles responses and notifications; the event buffer retains the latest 1,000 events.
- Requests time out after 30 seconds. Failed, cancelled, or timed-out requests are never replayed automatically.
- After a transport failure, the next API call reconnects and authenticates. Reopen projects and inspect their state before repeating an edit: the previous request may already have changed the project.
- Idle disconnects do not trigger background reconnects. Restarting the MCP process clears its event history.
- Tool results are returned as text containing JSON, including preview results.

## Validation

Run the transport unit tests without a running Concat app:

```sh
.venv/bin/python -B -m unittest test_bridge -v
```

These exercise connection reuse, concurrent callers, event delivery, disconnects, timeouts, cancellation, token redaction, and event buffer overflow using a local mock TCP server.

For an integration smoke test, start Concat, enable Remote, and configure `.env`, then run:

```sh
.venv/bin/python -B smoke_test.py
```

The smoke test starts a real MCP client, checks the 22 tools, calls read-only tools against Concat, confirms connection reuse, reads the command reference, and checks validation errors. It does not print project data or secrets.

Earlier validation on 2026-10-13 checked the tagged API contract and probed all 21 methods against Concat 0.2.6. Read methods succeeded; mutation methods were recognized through missing-required-field errors without changing existing projects. This verifies method recognition, **not every editing or export workflow**.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Server fails to start | Verify Python 3.11+, the `.venv` dependencies, the absolute launcher path, and a nonempty token. If needed, restore launcher permissions with `chmod +x start.sh`. |
| Authentication fails | Match `.env` to the token shown in Concat, then reconnect the MCP server. |
| Connection refused or timeout | Keep Concat running, enable Remote, and match its port. Inspect project state before retrying a timed-out edit. |
| Unsupported version | Use Concat 0.2.6 / API 0.2; other versions require contract validation and a bridge update. |
| Project access fails | Explicitly open the project in the API session and check Concat's access restrictions. |
| Export appears unfinished | Poll `concat_events`; inspect completion/failure events and the dropped-event count. |

## Repository layout

```text
concat_mcp.py     MCP server, tool definitions, and JSON-RPC transport
start.sh          Launcher using the local virtual environment
requirements.txt Python dependencies
.env.example     Configuration template without credentials
edit-commands.md  Bundled upstream edit command reference
test_bridge.py   Transport unit tests with a mock server
smoke_test.py    Read-only MCP integration smoke test
test_concat.py   Standalone TCP authentication/version diagnostic
```

## Contributing

Keep changes consistent with the supported Concat API contract and run the transport tests. For tool or schema changes, validate against the tagged upstream definitions and run the integration smoke test. Describe which editing/export workflows were actually tested, and use disposable projects when testing mutations.

## Attribution and license status

[`edit-commands.md`](edit-commands.md) contains upstream Concat source with its original **AGPL-3.0-or-later** copyright and license notices preserved. This repository currently has no separate license file for the bridge code.

Upstream references:

- [Concat repository](https://github.com/jub0t/concat)
- [Concat 0.2.6 API message definitions](https://github.com/jub0t/concat/blob/v0.2.6/src/crates/concat-api/src/message.rs)
- [Concat 0.2.6 edit commands](https://github.com/jub0t/concat/blob/v0.2.6/src/crates/concat-project/src/commands/mod.rs)
- [Concat JSON-RPC transport documentation](https://concatenate.pages.dev/docs/transports/json-rpc/)
