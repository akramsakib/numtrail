"""Module packages.

Everything here registers itself through :func:`numtrail.registry.module`; the
engine finds modules by walking this package. Layout:

    legacy/  uncovered public endpoints (web signup / login flows)
    api/     keyed lookup APIs (env var per module)
"""
