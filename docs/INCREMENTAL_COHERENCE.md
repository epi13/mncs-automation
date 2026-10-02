# Native affected-pass scheduling

`native/mncs/automation/coherence.mncs` owns current/affected/recovery/bootstrap
classification over bounded input subscriptions and fact observations. Its
`route_batch` function accepts up to sixteen eight-scalar rows, returning one
ordered disposition per row. Unknown observation chains require reconciliation;
missing established results require bootstrap. An unrelated selected scope stays
quiet. Declared input intersection, an existing revisit event or a matured
boundary deadline can wake established work.

`native/mncs/automation/revisit.mncs` is the shared generic wait/event law.
Projection's public `revisit_tick` delegates to it, preserving its existing
wait/event vocabulary and all established behavior. The coherence classifier
uses the same law, rather than another host retry implementation.

Environment owns subscription transport and invokes domain owners. It does not
interpret a scheduled pass as permission to repair or as a verification verdict.
`classify_files` classifies declaration spelling before prose and conservatively
classifies other files as source. It makes no assertion about semantic function
impact; Language Service remains that authority.

Native regression cases live in `tests/test_coherence.py`. For an isolated
worktree, existing integration tests accept `MNCS_WORKSPACE_ROOT`; the native
transport's existing `MNCS_TEST_NATIVE` supplies Test-owned assertion libraries.
Use a writable `MNCS_CACHE_DIR` when the default user cache is read-only. These
are fixture/provider addresses, not equivalence identities.
