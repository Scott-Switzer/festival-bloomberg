"""The Festival Intelligence terminal — read-only information product server.

``server`` exposes the read models from
:mod:`festival_bloomberg.intelligence.readmodels` over plain HTTP and serves
the static single-page app from ``apps/terminal/static``. Request handlers are
strictly read-only; the activity tape and provider health are written by the
OA driver, never by the terminal itself.
"""
