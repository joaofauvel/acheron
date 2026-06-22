"""Acheron worker SDK — the blueprint for Layer 8 real GPU workers.

Public surface re-exports are filled in by later tasks as modules land.
Importing this package must not require runpod/torch/etc. — those deps are
imported lazily by the modules that need them so unit tests of pure types
(handler, artifacts, settings) work without GPU SDKs installed.
"""
