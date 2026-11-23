"""Keyed lookup APIs.

These answer a different question from the web flows: not *does an account
exist* but *is this line real, and what is it*. Each module reports
``semantic="number_valid"`` so the output never conflates the two.
"""
