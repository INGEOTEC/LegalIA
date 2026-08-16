"""dofjson.get_nota() and dofjson.get_notas() are the package's unified
entry point for reading the DOF: SIDOF (dofjson.client) first, falling back
to the DOF's own website (dofjson.dofweb) when SIDOF has nothing (see
dofjson.api). Every other function that needs a note or a day's index
should call these two instead of using dofjson.client/dofjson.dofweb
directly."""

from dofjson.api import get_nota, get_notas

__version__ = "0.4.1"

__all__ = ["get_nota", "get_notas"]
