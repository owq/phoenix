phoenix bitcoin miner with added Stratum support and simple GUI
=======

Base: Phoenix Bitcoin Miner 2.0

1. Stratum protocol support (use stratum:// instead of http://)
2. Simple GUI with minimize to tray
3. New options in example.cfg

Tested on Windows 8 and Python 2.7

quick start guide
=======
Copy over doc/example.cfg and rename it to phoenix.cfg in the main directory.
Edit phoenix.cfg with your options.
Start phoenix.py or phoenix-gui.pyw (python modules needed: twisted, zope, numpy, pyopencl, pygtk{for gui})

py2exe
=======
python setup-py2exe.py py2exe
copy phoenix2\plugins to dist\plugins
copy phoenix2\gui to dist\phoenix2\gui
