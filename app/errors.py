"""Explicit error types for the API boundary.

The global handlers used to map **every** ``KeyError`` to a 404 and every
``ValueError`` to a 400, with the exception's message as the client-visible
detail. So an incidental ``KeyError`` from a dictionary bug anywhere in the
request path became a "not found" carrying an internal key name, and never
produced a stack trace — the bug was invisible in the logs and misleading on
screen.

These say what they mean. ``NotFound`` subclasses ``KeyError`` so the service
layer's own ``except KeyError`` blocks still catch a missing entity; only the
boundary treats them differently.
"""


class NotFound(KeyError):
    """The requested thing does not exist. -> 404"""

    def __str__(self) -> str:            # KeyError.__str__ quotes its argument
        return str(self.args[0]) if self.args else ""


class Invalid(ValueError):
    """The request was well-formed but asks for something impossible. -> 400"""
