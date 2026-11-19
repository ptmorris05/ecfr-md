"""A web navigator for the CFR, run on this computer: ``ecfr-md serve``.

Pages mirror ecfr.gov's paths (``/current/title-45/section-164.502``,
``/on/2020-01-01/title-45/part-164``) and fetch text through an
:class:`~ecfr_md.client.ECFR` and its cache, so anything read once is there
offline. Every page is also Markdown and JSON (``.md``, ``.json``), with
``/llms.txt`` and ``/openapi.json`` for programs. Needs the ``web`` extra.

>>> from ecfr_md import ECFR
>>> from ecfr_md.web import create_app
>>> app = create_app(ECFR())          # an ASGI app, for uvicorn or any server
"""

from ecfr_md.web.app import create_app

__all__ = ["create_app"]
