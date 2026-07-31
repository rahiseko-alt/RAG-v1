# Workbench acceptance evidence

## Automated tests

- `python -m pytest -q`: 39 passed
- `node --check src/api/static/app.js`: passed
- `node tests/e2e/workbench-e2e.cjs`: PASS
- Playwright viewports: desktop 1280x900, mobile 390x844

## Real API run

- Question: `東堂葵の術式は何ですか？`
- Result: `blocked`
- Candidate answer in response/UI: not exposed
- NG accordion: `回答照合`
- Blocked accordion: `出荷判定`
- Process steps: 8

## Langfuse

- Local status: `flushed`
- Cloud API status: `confirmed`
- Same trace contains: `retrieve`, `generate`, `verify`, `gate`
- Sanitized evidence: `langfuse-evidence.json`
- Local audit screenshot: `workbench-audit.jpg`

The in-app browser was not signed in to Langfuse Cloud, so the direct Cloud page displayed an access error. Remote existence and observation names were verified through the configured Langfuse API and then displayed in the local audit UI.
