"""Country and sector packs (ADR-0010). A pack imports core module services and registers into
their registries; core modules never import a pack.

``register_all`` runs each pack's ``register.py`` once, from the process entry points (the app
factory and the worker), so the registries are filled before anything reads them.
"""

from app.platform.db import import_from_each_module


def register_all() -> None:
    import_from_each_module("register")
