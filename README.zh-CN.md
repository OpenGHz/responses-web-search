# responses-web-search：通过 Responses API 搜索网页的 MCP

[English](README.md) | 简体中文

一个本地 stdio MCP，给 Claude Code 提供 `web_search` 工具。它调用中转站上 OpenAI Responses API 内置的 `web_search` 做真实搜索，返回答案、引用链接、查阅过的全部来源和搜索记录。

**适用场景：** Claude Code 通过第三方中转站（`ANTHROPIC_BASE_URL`）使用。很多中转站上，Claude 自己的搜索用不了：内置 WebSearch 会静默返回空结果，Claude 以为只是没搜到，就换关键词连续重试。如果同一个中转站也提供 OpenAI 的 `/v1/responses`，它的 web_search 用 GPT 模型（默认 `gpt-6.1-sol`）就能真正执行搜索。分工是：Claude 决定什么时候搜、读结果；搜索本身和 `answer` 里的摘要由 GPT 完成。

## 文件

| 文件 | 作用 |
|---|---|
| `server.py` | MCP server，只用 Python 标准库 |
| `helper.py` | 安装和验证用的子命令，每个对应下面一步，都不会打印密钥 |
| `TROUBLESHOOTING.zh-CN.md` | 某一步 FAIL 时查这里（[English](TROUBLESHOOTING.md)） |

## 安装

这一节是写给 agent 执行的：让 Claude Code "按 README 完成配置"即可。

**规则：**
- 密钥只存在 `~/.claude/settings.json` 的 `env` 里，或同名环境变量里。读这个文件时只看字段名，值一律掩码。不要把密钥写进代码、配置、命令行或日志。`helper.py` 已经做了掩码。
- 改配置前先备份。`register` 和 `deny-websearch` 会自动备份并打印备份路径，记下来，最后汇报给用户。
- 按顺序执行。每一步达到完成标准后再进入下一步。

**前提：**
- 这台电脑上的 Claude Code 已经通过中转站正常工作，即 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_AUTH_TOKEN` 在 `~/.claude/settings.json` 的 `env` 里。
- 有 `python3`（3.8 及以上，不用装包）和 `claude` 命令。
- 系统是 macOS 或 Linux。

1. **放置文件。**
   `git clone https://github.com/OpenGHz/responses-web-search.git ~/.claude/mcp-servers/responses-web-search`
   如果本文件夹已经在别处（例如拷贝过来的），直接复制到这个路径。放在其他位置也可以，注册时会使用它的绝对路径，只是之后不能再移动。后面所有命令都在这个目录下运行。
   完成标准：该目录下有 `server.py` 和 `helper.py`。

2. **预检：中转站支不支持 Responses API 的 web_search。**
   `python3 helper.py check`
   完成标准：输出 `PASS`，且至少有一行 `search [completed]`。
   如果 FAIL，按 `TROUBLESHOOTING.zh-CN.md` 处理。如果确认不支持（换了模型也拿不到 completed 的 web_search_call），就停下，告诉用户，不要注册。

3. **注册 MCP**（user scope，所有项目都能用）。
   `python3 helper.py register`
   如果第 2 步换过模型，或用户指定了默认推理强度、模型允许列表，先 `export` 对应的 `RESPONSES_WEB_SEARCH_MODEL`、`RESPONSES_WEB_SEARCH_EFFORT`、`RESPONSES_WEB_SEARCH_ALLOWED_MODELS`（见下方"工具说明"），并用同样的变量重跑一次第 2 步，PASS 后再运行这一步。这些值会写进 MCP 配置的 env，它们不是密钥。
   完成标准：输出 `PASS: registered and Connected`，且 `changed top-level keys` 只有 `mcpServers`。如果输出的是 `did not exist`，说明 `~/.claude.json` 是这次新建的，其他 key 本来就会有。如果已有文件里有别的 key 变了，通常是正在运行的 Claude Code 同时写了这个文件，核对后汇报给用户。

4. **测内置 WebSearch 能不能真的搜。**
   `python3 helper.py builtin`
   - `BROKEN`（searchCount=0，没有结果）：去做第 5 步。
   - `WORKS`：内置的能用，跳过第 5 步。告诉用户内置 WebSearch 在这台电脑上能用，问是否仍要禁用。
   - `NOT VISIBLE`：已经禁用过了，跳过第 5 步。
   - `INCONCLUSIVE`：再跑一次。还是这样就汇报给用户。

5. **禁用内置 WebSearch。**
   `python3 helper.py deny-websearch`
   原理：在 `~/.claude/settings.json` 的 `permissions.deny` 里加上 `"WebSearch"`。Claude Code 会把这个工具从 Claude 的工具列表里拿掉，Claude 就不会先去试它、连续失败。手动做的话，就是加上这一项：
   ```json
   "permissions": { "deny": ["WebSearch"] }
   ```
   只想在单次会话里禁用，可以用 `claude --disallowedTools WebSearch`。
   完成标准：diff 里只多了 `"WebSearch"`，并输出 `PASS`。

6. **端到端验证。** 新开一个 `claude -p` 会话，让 Claude 自己用这个工具真实搜索一次。
   `python3 helper.py e2e`
   完成标准：输出 `PASS`。如果做过第 5 步，还要看到 `tools visible: WebSearch=False`。

7. **密钥泄漏扫描。**
   `python3 helper.py leaks`
   完成标准：输出 `PASS: token appears in none of them`。

8. **汇报。** 告诉用户每一步的结果、备份文件路径和使用的模型。提醒用户重启已经打开的 Claude Code 会话，因为新配置只对新会话生效。

## 工具说明

`web_search` 的参数：
- `query`：问题或关键词。
- `allowed_domains`：可选。只在这些域名里搜，包括子域名，例如 `["python.org"]`。
- `model`：可选。这次搜索用的模型，不传就用 `RESPONSES_WEB_SEARCH_MODEL`。
- `reasoning_effort`：可选，`low` / `medium` / `high` / `xhigh` / `max` 之一。不传就用 `RESPONSES_WEB_SEARCH_EFFORT`，它也没设时用模型自己的默认值。工具描述会引导 agent 平时不传，只在需要多方核对时调高。

调用示例：`web_search(query="...", model="gpt-6.1-sol", reasoning_effort="high")`。

返回值是一份 JSON（`structuredContent`），Claude Code 交给模型的就是它：

| 字段 | 内容 |
|---|---|
| `answer` | GPT 根据搜索结果写的答案 |
| `citations` | 答案里引用的链接，`{title, url}` |
| `sources` | 这次搜索查阅过的全部 URL |
| `searches` | 搜索记录：每个 web_search_call 的 `id`、`status`、`action`（search / open_page / find_in_page）、`query` / `queries` / `url`、`source_count` |
| `model`、`reasoning_effort`、`response_id`、`status` | 这次 Responses 调用本身的信息，`reasoning_effort` 是 API 实际采用的推理强度 |
| `warning` | 只在请求的推理强度没有生效（被换成了默认值）时出现 |

行为：
- 每次调用都强制真实搜索（`tool_choice: "required"`）。不加这一项的话，简单问题会直接凭记忆回答，不产生 web_search_call。
- 没有 completed 的 web_search_call 时返回错误（`isError`），不会把没经过搜索的答案当作搜索结果。
- 认证在运行时读取 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`：先看环境变量，没有再读 `~/.claude/settings.json` 的 `env`。MCP 配置里不存密钥。如果错误信息里出现密钥，会被替换成 `***`。
- 可用的环境变量（都是调用时不传参数的默认值；agent 传了参数就以参数为准）：
  - `RESPONSES_WEB_SEARCH_MODEL`：默认 `gpt-6.1-sol`。
  - `RESPONSES_WEB_SEARCH_ALLOWED_MODELS`：逗号分隔，限制 agent 能通过 `model` 参数选哪些模型。`RESPONSES_WEB_SEARCH_MODEL` 总是允许的。不设置则不限制。
  - `RESPONSES_WEB_SEARCH_EFFORT`：推理强度。不设置时不发送 `reasoning` 字段，使用模型自己的默认值，gpt-6.1-sol 是 `medium`。gpt-6.1-sol 支持 `low`、`medium`、`high`、`xhigh`、`max`。强度越高，搜索和核对越充分，但更慢、更贵。不支持的值（如 `none`、`minimal`）在作者测试的中转站上，带 web_search 时会返回 503，不带工具时会被静默换成默认值。这两种情况 `helper.py check` 都会报 FAIL，所以改强度后先跑一次 `check`。
  - `RESPONSES_WEB_SEARCH_TIMEOUT_SEC`：默认 180。调高推理强度时可以相应调大。
- 已经注册后想改默认模型、默认强度或允许列表：先 `claude mcp remove responses-web-search -s user`，带着新的环境变量重跑第 2、3 步，再重启 Claude Code。

## 撤销

- 移除 MCP：`claude mcp remove responses-web-search -s user`
- 恢复内置 WebSearch：从 `~/.claude/settings.json` 的 `permissions.deny` 里删掉 `"WebSearch"`。
- 也可以直接用第 3、5 步打印的备份文件还原（`~/.claude.json.bak-*`、`~/.claude/settings.json.bak-*`）。

## 许可

MIT，见 `LICENSE`。
