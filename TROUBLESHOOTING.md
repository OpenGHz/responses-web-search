# 排障

按报错文本找对应的条目。命令都在本目录下运行，全程不要打印密钥。

## `HTTP 403 ... error code: 1010`

这是 Cloudflare 按 User-Agent 拦截。Python urllib 的默认 UA 会被拦，`server.py` 已经改发自己的 UA（`responses-web-search/1.0.0`）。如果新中转站连这个也拦，把 `server.py` 里的 `USER_AGENT` 改成常见客户端的格式，例如 `curl/8.5.0`（在作者测试的中转站上验证过能通过）。

## `HTTP 503 ... no_available_providers`

中转站没有把这个模型接到 `/v1/responses` 上，需要换模型：

1. 列出这个 token 能看到的模型：
   ```bash
   python3 -c 'import server,json,urllib.request as u
   b,t=server.load_credentials()
   r=u.Request(b+"/v1/models",headers={"Authorization":"Bearer "+t,"User-Agent":server.USER_AGENT})
   print([m["id"] for m in json.load(u.urlopen(r,timeout=30)).get("data",[])])'
   ```
2. 这个列表并不完整。在作者测试的中转站上，列表里只有 `deepseek-*`，它们在 `/v1/responses` 上全部 503。反而是不在列表里的 `gpt-6.1-sol` 能用。所以也要试 GPT 系列的模型名，比如用户 Codex 配置（`~/.codex/config.toml` 的 `model`）里的那个。
3. 逐个试：`RESPONSES_WEB_SEARCH_MODEL=<模型> python3 helper.py check`。找到 PASS 的模型后，带着同一个环境变量运行 README 第 3 步的 `register`。

## `HTTP 401`，或不是 1010 的 `403`

token 无效或没有权限。先确认 Claude Code 本身能正常对话。token 来自 `~/.claude/settings.json` 的 `env.ANTHROPIC_AUTH_TOKEN`，或同名环境变量。排查时只比较长度或前 3 个字符，不要打印整个 token。

## `no completed web_search_call`

请求成功了，但模型没有真正去搜，说明这个模型或中转站不执行 web_search。按上面的方法换模型。都不行就说明不支持，停下并告诉用户。

## `ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN not found`

这台电脑上的 Claude Code 不是通过 settings.json 的 env 或环境变量连接中转站的，例如用 OAuth 登录的官方账号。这个 MCP 依赖中转站，告诉用户实际情况，不要继续。

## `register` 后不是 Connected

- 手动运行 `python3 server.py`，正常情况下它会安静地等待输入（Ctrl-C 退出）。如果报错，按报错处理。
- Claude Code 的 PATH 里可能没有 `python3`。先 `claude mcp remove responses-web-search -s user`，再用绝对路径注册：
  `claude mcp add responses-web-search --scope user -- "$(command -v python3)" "$PWD/server.py"`

## `builtin` 一直是 INCONCLUSIVE

说明 Claude 没有按要求调用 WebSearch。可以手动跑：
`claude -p '用 WebSearch 搜一次 latest Python release' --allowedTools WebSearch --output-format stream-json --verbose`
然后在输出里找 `tool_use_result` 的 `searchCount`，0 表示没有真正搜索。

## `e2e` 里 Claude 先用了别的工具

WebFetch，或描述里声明要处理搜索的 skill，也可能被先调用。在 `claude -p` 里它们没有权限，会失败；交互式会话里则会向用户请求授权。只要 MCP 最终被调用并成功，第 6 步就算 PASS。要不要限制其他工具由用户决定。

## 背景：为什么不用 Claude 自己搜

以下是 2026-10 在作者使用的中转站上测得的结果：
- **Claude Code 内置的 WebSearch：** 返回 `is_error=false`，但 `searchCount: 0, results: []`，属于静默失败。Claude 以为只是"没搜到"，就反复换关键词重试，这就是"连续失败"的来源。
- **直接调 `/v1/messages` 的 web_search 服务端工具：** 对 Claude 模型的直接请求一律 503，不带工具也一样。同一时刻 Claude Code 本身却能正常工作，看起来中转站只放行 Claude Code 客户端。不要通过伪装成 Claude Code 客户端来绕过。
- 换了中转站，结论可能不一样，所以 README 第 4 步每次都要实测。
