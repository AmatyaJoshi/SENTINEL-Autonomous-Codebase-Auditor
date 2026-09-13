Write a one-paragraph explanation of this verified bug for a pull-request reviewer who has not
seen the code. Plain language, no hedging, no marketing. Say what goes wrong, with which input, what
the consequence is, and what the fix changes. Under 120 words.

Category: {{category}} · Severity: {{severity}} · Location: `{{file}}:{{line_start}}` ({{symbol}})
Description: {{description}}
Triggering input: {{hypothesis}}
Test that proves it:
```
{{test_code}}
```
Patch:
```
{{patch_diff}}
```

Return JSON matching the ExplainOutput schema.
