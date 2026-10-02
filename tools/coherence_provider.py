#!/usr/bin/env python3
"""Automation-owned retained coherence artifact capability.

Forge owns bounded build/execution transport; Commons admits provenance;
Doctor admits confined remediation. Automation supplies its build inputs.
"""
from __future__ import annotations
import json
from pathlib import Path
from mncs_forge.provider_artifacts import ProviderArtifact, digest

_instances = {}

def provider(*, roots, compiler, embed, cache):
    root = Path(roots['mncs-automation']).resolve()
    specification = json.loads((root / '.mncs/coherence-artifact.json').read_text())
    key = (tuple(sorted((name, str(Path(path).resolve())) for name, path in roots.items())),
           digest(specification), str(Path(cache).resolve()), str(compiler), str(embed))
    instance = _instances.get(key)
    if instance is None:
        instance = ProviderArtifact(specification, roots=roots, compiler=compiler, embed=embed, cache=cache)
        _instances[key] = instance
    return instance

def call(*, roots, compiler, embed, cache, function, arguments):
    return provider(roots=roots, compiler=compiler, embed=embed, cache=cache).call(
        'mncs.automation.coherence.v1', function, arguments)

def close():
    for instance in _instances.values():
        instance.close()
    _instances.clear()
