# responses-web-search: web search for Claude Code over the Responses API

English | [简体中文](README.zh-CN.md)

A local stdio MCP server that gives Claude Code a `web_search` tool. It runs real searches through the built-in `web_search` tool of the OpenAI Responses API on your relay, and returns the answer, cited links, every source consulted, and the search record.

**When to use it:** you run Claude Code through a third-party relay (`ANTHROPIC_BASE_URL`). On many relays Claude's own search doesn't work: the built-in WebSearch silently returns empty results, and Claude, taking that as "nothing found", keeps retrying with reworded queries. If the same relay also serves OpenAI's `/v1/responses`, its web_search runs real searches with a GPT model (default `gpt-6.1-sol`). The split: Claude decides when to search and reads the results; the search itself and the summary in `answer` are done by GPT.

## Files

| File | Purpose |
|---|---|
| `server.py` | The MCP server; Python standard library only |
| `helper.py` | Install and verify subcommands, one per step below; none of them prints the token |
| `TROUBLESHOOTING.md` | Look here when a step FAILs ([简体中文](TROUBLESHOOTING.zh-CN.md)) |

## Install

This section is written for an agent to execute: tell Claude Code to "set this up following README.md".

**Rules:**
- The token lives only in the `env` block of `~/.claude/settings.json` or in the environment variable of the same name. When reading that file, look at key names only and mask every value. Keep the token out of code, config, command lines and logs; `helper.py` already masks it.
- Back up before changing config. `register` and `deny-websearch` back up automatically and print the backup path; note it and include it in the final report.
- Run the steps in order. Move on only once the current step meets its completion criterion.

**Prerequisites:**
- Claude Code on this machine already works through a relay, i.e. `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` are in the `env` block of `~/.claude/settings.json`.
- `python3` (3.8+, no packages) and the `claude` command are available.
- macOS or Linux.

1. **Place the files.**
   `git clone https://github.com/OpenGHz/responses-web-search.git ~/.claude/mcp-servers/responses-web-search`
   If this folder is already somewhere else (copied over, for example), copy it to that path. Another location also works, but registration records its absolute path, so the folder can't be moved afterwards. Run every later command from this directory.
   Done when: the directory contains `server.py` and `helper.py`.

2. **Pre-check: does the relay support Responses API web_search?**
   `python3 helper.py check`
   Done when: the output says `PASS` and has at least one `search [completed]` line.
   On FAIL, follow `TROUBLESHOOTING.md`. If support is ruled out (no model yields a completed web_search_call), stop and tell the user; do not register.

3. **Register the MCP server** (user scope, available in every project).
   `python3 helper.py register`
   If step 2 needed a different model, or the user asked for a default reasoning effort or a model allowlist, first `export` the matching `RESPONSES_WEB_SEARCH_MODEL`, `RESPONSES_WEB_SEARCH_EFFORT`, `RESPONSES_WEB_SEARCH_ALLOWED_MODELS` (see "Tool reference" below), rerun step 2 with the same variables, and run this step once it PASSes. These values are written into the MCP config's env; they are not secrets.
   Done when: the output says `PASS: registered and Connected` and `changed top-level keys` lists only `mcpServers`. If the output says `did not exist`, `~/.claude.json` was just created and its other keys are expected. If other keys of an existing file changed, a running Claude Code instance probably wrote it at the same time; check, and report it to the user.

4. **Test whether the built-in WebSearch really searches.**
   `python3 helper.py builtin`
   - `BROKEN` (searchCount=0, no results): go to step 5.
   - `WORKS`: the built-in tool works, so skip step 5. Tell the user the built-in WebSearch works on this machine and ask whether to disable it anyway.
   - `NOT VISIBLE`: already disabled; skip step 5.
   - `INCONCLUSIVE`: run it again. If it stays inconclusive, report it to the user.

5. **Disable the built-in WebSearch.**
   `python3 helper.py deny-websearch`
   How it works: it adds `"WebSearch"` to `permissions.deny` in `~/.claude/settings.json`. Claude Code then removes the tool from Claude's tool list, so Claude stops trying it first and failing repeatedly. To do it by hand, add this entry:
   ```json
   "permissions": { "deny": ["WebSearch"] }
   ```
   To disable it for a single session only, use `claude --disallowedTools WebSearch`.
   Done when: the diff adds only `"WebSearch"` and the output says `PASS`.

6. **End-to-end check.** A fresh `claude -p` session has Claude run one real search with this tool.
   `python3 helper.py e2e`
   Done when: the output says `PASS`. If step 5 ran, it must also show `tools visible: WebSearch=False`.

7. **Token leak scan.**
   `python3 helper.py leaks`
   Done when: the output says `PASS: token appears in none of them`.

8. **Report.** Tell the user each step's result, the backup paths, and the model in use. Remind them to restart any open Claude Code sessions, since new config only takes effect in new sessions.

## Tool reference

Arguments of `web_search`:
- `query`: the question or keywords.
- `allowed_domains`: optional. Search only these domains, subdomains included, e.g. `["python.org"]`.
- `model`: optional. The model for this search; defaults to `RESPONSES_WEB_SEARCH_MODEL`.
- `reasoning_effort`: optional, one of `low` / `medium` / `high` / `xhigh` / `max`. Defaults to `RESPONSES_WEB_SEARCH_EFFORT`, or to the model's own default when that is unset too. The tool description steers agents to leave it unset normally and raise it only when sources need cross-checking.

Example: `web_search(query="...", model="gpt-6.1-sol", reasoning_effort="high")`.

The result is a JSON object (`structuredContent`), which is what Claude Code hands to the model:

| Field | Contents |
|---|---|
| `answer` | The answer GPT wrote from the search results |
| `citations` | Links cited in the answer, as `{title, url}` |
| `sources` | Every URL this search consulted |
| `searches` | The search record: each web_search_call's `id`, `status`, `action` (search / open_page / find_in_page), `query` / `queries` / `url`, `source_count` |
| `model`, `reasoning_effort`, `response_id`, `status` | About the Responses call itself; `reasoning_effort` is the effort the API actually applied |
| `warning` | Present only when the requested reasoning effort was not applied (replaced by the default) |

Behavior:
- Every call forces a real search (`tool_choice: "required"`). Without it, simple questions get answered from memory with no web_search_call.
- If no web_search_call completed, the call returns an error (`isError`) instead of presenting an ungrounded answer as a search result.
- Credentials are read at runtime: `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` from the environment first, then from the `env` block of `~/.claude/settings.json`. The MCP config stores no secrets, and a token that appears in an error message is replaced with `***`.
- Environment variables (defaults for calls that omit the arguments; an argument the agent passes takes precedence):
  - `RESPONSES_WEB_SEARCH_MODEL`: default `gpt-6.1-sol`.
  - `RESPONSES_WEB_SEARCH_ALLOWED_MODELS`: comma-separated; limits which models an agent may pick through the `model` argument. `RESPONSES_WEB_SEARCH_MODEL` is always allowed. Unset means no limit.
  - `RESPONSES_WEB_SEARCH_EFFORT`: reasoning effort. Unset, no `reasoning` field is sent and the model's own default applies (`medium` for gpt-6.1-sol). gpt-6.1-sol supports `low`, `medium`, `high`, `xhigh`, `max`. Higher effort searches and cross-checks more thoroughly but is slower and costs more. On the author's relay, unsupported values (such as `none` or `minimal`) return 503 when web_search is attached, and are silently replaced with the default when no tool is attached. `helper.py check` FAILs in both cases, so run `check` after changing the effort.
  - `RESPONSES_WEB_SEARCH_TIMEOUT_SEC`: default 180. Raise it when you raise the reasoning effort.
- To change the default model, default effort or allowlist after registering: run `claude mcp remove responses-web-search -s user`, rerun steps 2 and 3 with the new variables, then restart Claude Code.

## Undo

- Remove the MCP server: `claude mcp remove responses-web-search -s user`
- Restore the built-in WebSearch: delete `"WebSearch"` from `permissions.deny` in `~/.claude/settings.json`.
- Or restore from the backups printed in steps 3 and 5 (`~/.claude.json.bak-*`, `~/.claude/settings.json.bak-*`).

## License

MIT; see `LICENSE`.
