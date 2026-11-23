"""Checks against public web endpoints.

These were the whole of the original tool. Public endpoints are an arms race:
each one below can start answering with a captcha at any moment, which is why a
module reports ``blocked`` rather than guessing. Run from a residential IP, or
rotate with ``--proxy``, and expect to re-verify the flows now and then.
"""
