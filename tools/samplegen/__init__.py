"""Synthetic medical sample documents + ground truth for the medextract eval.

Each sample is defined once as structured data (``SampleDoc``). The same data feeds the Jinja
template that renders the document *and* the truth builder that writes ``truth.json``, so the
truth can never drift from what is printed.
"""
