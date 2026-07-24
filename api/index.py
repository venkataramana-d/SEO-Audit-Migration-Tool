"""Vercel serverless entry point — exposes the Flask WSGI app.

NOTE: Vercel is serverless. The interactive pages render, but live crawling/
auditing does NOT work here (background threads are killed when the function
returns and job state isn't shared between invocations). Run locally or on an
always-on host (Render/Railway/VPS) for the actual audit features.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Vercel's @vercel/python serves this WSGI `app`)
