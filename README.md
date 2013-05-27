phoenix bitcoin miner with added Stratum support and simple GUI
=======

Base: Phoenix Bitcoin Miner 2.0

Additions:

1. Stratum protocol support (use stratum:// instead of http://)
2. Simple GUI with minimize to tray
3. New options in example.cfg

Tested on Windows 8 and Python 2.7

quick start guide
=======
1. Copy over doc/example.cfg and rename it to phoenix.cfg in the main directory.
2. Edit phoenix.cfg with your options.
3. Start phoenix.py or phoenix-gui.pyw (python modules needed: twisted, zope, numpy, pyopencl, pygtk{for gui})

py2exe
=======
1. python setup-py2exe.py py2exe
2. copy phoenix2\plugins to dist\plugins
3. copy phoenix2\gui to dist\phoenix2\gui
