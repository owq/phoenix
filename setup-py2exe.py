#!/usr/bin/env python

from distutils.core import setup
import py2exe

# Note: on Windows two modules had problems
# 1. zope. Need to create an __init__.py in the module directory
# Refer to http://stackoverflow.com/questions/7816799/getting-py2exe-to-work-with-zope-interface, second answer
# 2. pyopencl. Need to fix _find_pyopencl_include_path to return correct path.
# Refer to http://stackoverflow.com/questions/12653568/no-module-named-pyopencl-py2exe

setup(
      console=['phoenix.py'],
      windows=['phoenix-gui.pyw'],
      options={
        "py2exe":{
            "bundle_files": 3,
            "includes": "zope.interface, cairo, gio, pango, atk, pangocairo, pyopencl, numpy",
            "dll_excludes": "MSVCP90.DLL"
         }
      }
     )
