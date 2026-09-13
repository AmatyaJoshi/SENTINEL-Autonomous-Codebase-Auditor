## Target
Repository language: {{language}}
File: `{{file}}`
Symbol focus: {{symbol}}
Why this target was chosen: {{reason}}

## Analyzer hits on this file
{{analyzer_hits}}

## Starting excerpt
```
{{excerpt}}
```

Investigate this target with at most {{max_tool_calls}} tool calls, then return HuntOutput JSON.
Use `read_file` to widen the excerpt, `get_callers` to learn how inputs arrive, `search_code` to find
related validation, and `run_snippet` to confirm a hypothesis when it can be tested in isolation.
