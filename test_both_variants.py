#!/usr/bin/env python3
"""Test both V1 and V2 prompts with the same seed."""
import sys, json, random, importlib.util
from pathlib import Path

sys.path.insert(0, '.')
base = Path('.')

# Load pipeline-v2.py (hyphenated filename)
spec = importlib.util.spec_from_file_location('pipeline_v2', base / 'pipeline-v2.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
generate_slides = mod.generate_slides

# Reset counter
counter_file = base / 'ab_counter.json'
counter_file.write_text('{}')

# V1
print('=== V1 (Educational List Style) ===')
r1, c1 = generate_slides('Tanda-tanda tubuh kebanyakan gula', ab_variant='v1')
for k, v in r1.items():
    if k.startswith('slide_'):
        print(f'\n{k}: {v[:200]}...' if len(v) > 200 else f'\n{k}: {v}')
print(f'\nClaims: {len(c1)} items')

print('\n\n=== V2 (Observational List Style) ===')
counter_file.write_text('{}')
r2, c2 = generate_slides('Tanda-tanda tubuh kebanyakan gula', ab_variant='v2')
for k, v in r2.items():
    if k.startswith('slide_'):
        print(f'\n{k}: {v[:200]}...' if len(v) > 200 else f'\n{k}: {v}')
print(f'\nClaims: {len(c2)} items')
