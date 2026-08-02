"""Region packages implementing the core boundary protocols (planning §4.2).

Everything country-specific — tax rules, wrappers, state pension, age
rules — lives under this package. The dependency direction is region →
core only: the core never imports region code (guard-tested), and no
policy figure appears outside a region's ``data/`` TOML files.
"""
