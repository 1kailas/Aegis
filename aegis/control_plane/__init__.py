"""Control plane: deterministic thermal guardian and policy execution.

This layer never calls AI models. It must stay safe and responsive on
its own so local-model outage or cloud absence cannot disable the
temperature and resource safeguards.
"""
