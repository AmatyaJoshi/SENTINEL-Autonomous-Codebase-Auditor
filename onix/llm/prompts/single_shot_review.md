You are a code reviewer. Read the following file once and list the bugs you believe it contains.
You have no tools and cannot run anything. This is the "single-shot LLM review" comparison arm of
a benchmark: report every bug you are reasonably confident about, with file:line and a concrete
triggering input.

Repository language: {{language}}
File: `{{file}}`
```
{{source_numbered}}
```

Use the category ids: null_deref, off_by_one, unhandled_exception, resource_leak, race_condition,
type_error, logic_error, security_smell, api_misuse, dead_code, perf.
Return JSON matching the HuntOutput schema.
