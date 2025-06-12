import os
import sys
sys.path.insert(0, os.path.abspath('../lnhistoryclient')) 

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',  # for Google/NumPy style docstrings
    'sphinx.ext.viewcode',
]

html_theme = "furo"