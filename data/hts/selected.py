import pandas as pd
import numpy as np

def configure(context):
    hts = context.config("hts")

    if hts == "entd":
        context.stage("data.hts.entd.reweighted", alias = "hts")
    else:
        raise RuntimeError(
            "Unknown HTS: %s (only 'entd' is supported; the Ile-de-France/Lyon/Nantes surveys were removed)" % hts)

def execute(context):
    return context.stage("hts")
