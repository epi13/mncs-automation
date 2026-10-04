# Provider coherence: discovery over copied paths

Owner: `mncs-automation` (consumer), `mncs-doctor` (provider identity).

## Rule

One owner publishes truth; consumers validate truth. Automation never
hardcodes another repository's layout as an expectation. Provider truth
flows through:

- provider descriptor and canonical identity;
- environment discovery results;
- Store-owned evidence;
- generated authoritative indexes.

## Declared source dependencies are not copies

`.mncs/coherence-artifact.json` names concrete source files such as
`mncs/doctor/provider.mncs`, but as *build inputs* to the compiled
coherence provider (module closure + admission sources), not as layout
expectations. When that file appeared to go missing, the cause was a
stale Doctor checkout on an old branch, not a moved provider: aligning
the checkout to `main` restored the file with no product change. The
correct response to "expected source missing" is to check checkout
freshness first, not to recreate the file for a stale test.

## End-to-end proof

Provider coherence is proven through the canonical environment with real
providers (stubs are for unit tests only):

```text
Doctor provider discovered
      |
Automation coherence recognizes it
      |
Forge/provider fixture builds, admits, executes
      |
receipt/result validates
```
