# Copyright (C) 2012 by jedi95 <jedi95@gmail.com> and
#                       CFSworks <CFSworks@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import sys
import imp
import os
import platform
import time
from weakref import WeakKeyDictionary

from twisted.internet import reactor, task, defer

from .. import backend
from ..backend.MMPProtocol import MMPClient
from ..backend.StratumProtocol import StratumClient

from .WorkQueue import WorkQueue
from .PhoenixLogger import *
from .KernelInterface import KernelInterface
from .PhoenixConfig import PhoenixConfig
from .PhoenixRPC import PhoenixRPC
from .PluginInterface import PluginInterface

from urlparse import urlparse
from ..gui.PhoenixGUILogger import PhoenixGUILogger 

class PhoenixCore(object):
    """The root-level object of a Phoenix mining instance."""

    # This must be manually set for Git
    VER = (2, 0, 0)
    VERSION = 'v%s.%s.%s' % VER

    def __init__(self, cfgFilename='phoenix.cfg', gui = None):
        self.kernelTypes = {}
        self.connection = None #Is a ClientBase
        self.connectionURL = None
        self.connected = False
        self.connectionType = 'none'
        self.failbackLoop = None

        if not hasattr(sys, 'frozen'):
            self.basedir = os.path.dirname(os.path.dirname(__file__))
        else:
            self.basedir = os.path.dirname(sys.executable)

        self.config = PhoenixConfig(cfgFilename)
        self.logger = PhoenixGUILogger(self) if gui else PhoenixLogger(self) 
        self.queue = WorkQueue(self)
        self.rpc = PhoenixRPC(self)

        self.pluginModules = {}

        self.pluginIntf = PluginInterface(self)
        self.plugins = {}

        self.kernels = {}
        self.interfaces = WeakKeyDictionary()
        self.deviceIDs = []
        self.deviceAutoconfig = {}

        self.idle = True
        self.lastMetaRate = 0
        self.lastRate = 0

        self.startTime = time.time()

        self._analysisMemo = {}

    def start(self):
        self._meta = {}

        self.logger.log('Welcome to Phoenix ' + self.VERSION)
        self.startTime = time.time()

        self.discoverPlugins()
        self.startAllKernels()
        self.startAutodetect()

        self.setMeta('os', '%s %s' % (platform.system(), platform.version()))

        self.configChanged()
        self.switchURL(self.config.get('general', 'backend', str))

        reactor.addSystemEventTrigger('before', 'shutdown', self._shutdown)

    def configChanged(self):
        self.rpc.start() # In case the ip/port changed...

    def _shutdown(self):
        self.stopAutodetect()
        self.switchURL(None)
        for kernel in self.kernels.values():
            if kernel is not None:
                kernel.stop()
        self.kernels = {}

    def loadPlugin(self, name, silent=False):
        if name in self.pluginModules:
            return

        plugindir = os.path.join(self.basedir, 'plugins')

        def importPlugin(name):
            self.loadPlugin(name, silent=True)
            if name not in self.pluginModules:
                raise ImportError('Dependency on a plugin that failed to load')
            else:
                # Inject it into the caller's namespace rather than return it.
                callerNamespace = sys._getframe().f_back.f_locals
                callerNamespace[name] = self.pluginModules[name]

        import __builtin__
        __builtin__.importPlugin = importPlugin

        try:
            file, filename, smt = imp.find_module(name, [plugindir])
            plugin = imp.load_module(name, file, filename, smt)
            self.pluginModules[name] = plugin
            if hasattr(plugin, 'PhoenixKernel'):
                self.kernelTypes[name] = plugin.PhoenixKernel
            else:
                self.plugins[name] = plugin.PhoenixPlugin(self.pluginIntf)
        except (ImportError, AttributeError):
            if not silent:
                self.logger.log('Failed to load plugin "%s" %s' % (name, plugindir))

    def discoverPlugins(self):
        plugindir = os.path.join(self.basedir, 'plugins')
        for name in os.listdir(plugindir):
            if name.endswith('.pyo') or name.endswith('.pyc'):
                if os.path.isfile(os.path.join(plugindir, name[:-1])):
                    continue
            name = name.split('.',1)[0] # Strip off . and anything after...
            self.loadPlugin(name)

    def startAutodetect(self):
        # NOTICE: It is legal to call this function more than once. If this
        # happens, kernels are expected to re-report the devices.
        for kernel in self.kernelTypes.values():
            if hasattr(kernel, 'autodetect'):
                kernel.autodetect(self._autodetectCallback)

    def stopAutodetect(self):
        for kernel in self.kernelTypes.values():
            if hasattr(kernel, 'stopAutodetect'):
                kernel.stopAutodetect()

    def redetect(self, terminate=False):
        if terminate:
            for devid in self.kernels.keys():
                devidset = None
                for idset in self.deviceIDs:
                    if devid in idset:
                        devidset = idset
                        break

                assert devidset is not None

                if not self.checkRules(devidset):
                    self.stopKernel(devid)
                    del self.kernels[devid] # Totally forget about it.
                    self.deviceIDs.remove(devidset)

        self.startAutodetect()

    def checkRules(self, ids):
        types = [x.split(':',1)[0] for x in ids]

        rules = self.config.get('general', 'autodetect', str, '')
        rules = rules.lower().replace(',', ' ').split()

        use = False
        for rule in rules:
            if rule.lstrip('-+') in types:
                use = not rule.startswith('-')

        return use

    def _autodetectCallback(self, device):
        device = device.lower()

        for idset in self.deviceIDs:
            if device in idset:
                if idset[0] in self.kernels:
                    return

        kernel, ranking, autoconfiguration, ids = self._analyzeDevice(device)

        if self.checkRules(ids):
            if self.startKernel(ids[0]):
                name = autoconfiguration.get('name', device)
                kernelName = [x for x,y in self.kernelTypes.items() if y ==
                              kernel][0]
                self.logger.debug('Detected [%s]: [%s] using %s (rating %s)' %
                                  (device, name, kernelName, ranking))

    def _analyzeDevice(self, device):
        if device in self._analysisMemo:
            return self._analysisMemo[device]

        ids = set()

        bestKernel = None
        bestRanking = 0
        bestConfig = None
        bestKernelID = device

        toAnalyze = [device]
        while toAnalyze:
            analyzing = toAnalyze.pop(0)
            assert analyzing not in ids
            ids.add(analyzing)

            for kernel in self.kernelTypes.values():
                if not hasattr(kernel, 'analyzeDevice'):
                    continue

                ranking, configuration, names = kernel.analyzeDevice(analyzing)

                if ranking > bestRanking:
                    bestRanking = ranking
                    bestKernel = kernel
                    if names:
                        bestKernelID = names[0]
                    else:
                        bestKernelID = analyzing
                    bestConfig = configuration

                for name in names:
                    if name not in ids and name not in toAnalyze:
                        toAnalyze.append(name)

        # We need to make sure the preferred ID comes first, so...
        ids.remove(bestKernelID)
        ids = [bestKernelID] + list(ids)

        self._analysisMemo[device] = (bestKernel, bestRanking, bestConfig, ids)

        return bestKernel, bestRanking, bestConfig, ids

    def switchURL(self, url):
        """Connects the Phoenix miner to a new URL immediately.

        Issue None to disconnect.
        """

        if self.connectionURL == url:
            return

        if self.connection is not None:
            self.connection.disconnect()
            self.connection = None
            self.onDisconnect() # Make sure the disconnect log goes through...

        if self.failbackLoop and not self.failbackLoop.running:
            self.failbackLoop.stop()
            self.failbackLoop = None

        if not url:
            return
        
        self.connectionURL = url

        self.connection = backend.openURL(url, self) #Opens a new connection (i.e. creates a new client)

        if isinstance(self.connection, MMPClient):
            self.connectionType = 'mmp'
        elif isinstance(self.connection, StratumClient):
            self.connectionType = 'stratum'
#             self.queue.queuesize = 20 #custom queue size for stratum, is okay because of clear_jobs notification
        else:
            self.connectionType = 'rpc'
        self.logger.refreshStatus()

        self.connection.setVersion('phoenix', 'Phoenix Miner', self.VERSION)
        for var, value in self._meta.iteritems():
            self.connection.setMeta(var, value)

        self.connection.connect()

    def getKernelConfig(self, devid):
        kernel = self.kernels.get(devid)
        if kernel:
            return self.interfaces[kernel].options

        options = {}
        for key, value in self.config.getsection(devid).items():
            options[key.lower()] = value

        # Autoconfiguration is enabled for devices that aren't in the config
        # file, and disabled (by default) for devices that are.
        inConfig = devid in self.config.listsections()
        if self.config.get(devid, 'autoconfigure', bool, not inConfig):
            autoconfig = dict(self.deviceAutoconfig.get(devid, {}))
            autoconfig.update(options)
            return autoconfig
        else:
            return options

    def startAllKernels(self):
        for section in self.config.listsections():
            if ':' in section: # It's a device if it contains a :
                if self.config.get(section, 'start_undetected', bool, True):
                    self.startKernel(section)

    def startKernel(self, device):
        """Start a brand-new kernel on 'device', passing an optional
        dictionary of kernel parameters.

        The newly-created kernel is returned.
        """

        device = device.lower()

        if self.config.get(device, 'disabled', bool, False):
            return

        kernelType, _, autoconfiguration, ids = self._analyzeDevice(device)

        for idset in self.deviceIDs:
            for devid in ids:
                if devid in idset:
                    if self.kernels.get(idset[0]) is not None:
                        return

        kernelOption = self.config.get(device, 'kernel', str, None)
        if kernelOption:
            kernelType = self.kernelTypes.get(kernelOption)
            if hasattr(kernelType, 'analyzeDevice'):
                _, autoconfiguration, _ = kernelType.analyzeDevice(device)
            else:
                autoconfiguration = {}

        if not kernelType:
            interface = KernelInterface(device, self,
                                        self.getKernelConfig(device))
            self.logger.dispatch(KernelFatalLog(interface,
                                                'No kernel; disabled.'))
            return

        self.deviceAutoconfig[device] = autoconfiguration

        interface = KernelInterface(device, self, self.getKernelConfig(device))
        kernel = kernelType(interface)
        interface.kernel = kernel

        if interface._fatal:
            # The kernel had a fatal error in initialization...
            return None

        self.kernels[device] = kernel
        self.interfaces[kernel] = interface

        ids.remove(device)
        ids.insert(0, device) # Canonical device MUST be first.
        for idset in self.deviceIDs:
            if device in idset:
                break
        else:
            self.deviceIDs.append(ids)

        kernel.start()

        if not interface._fatal:
            return kernel

    def stopKernel(self, device):
        """Stop an already-running kernel."""
        if device not in self.kernels or self.kernels[device] is None:
            return

        self.kernels[device].stop()
        self.kernels[device] = None

        self._recalculateTotalRate()

    def setMeta(self, var, value):
        self._meta[var] = value
        if self.connection is not None:
            self.connection.setMeta(var, value)

    def requestWork(self):
        if self.connection is not None:
            self.connection.requestWork()

    def _recalculateTotalRate(self):
        # Query all mining cores for their Khash/sec rate and sum.

        self.lastRate = 0
        if not self.idle:
            for kernel in self.kernels.values():
                if kernel is not None:
                    self.lastRate += self.interfaces[kernel].getRate()

        self.logger.dispatch(RateUpdateLog(self.lastRate))

        # Let's not spam the server with rate messages.
        if self.lastMetaRate+30 < time.time():
            self.setMeta('rate', self.lastRate)
            self.lastMetaRate = time.time()

    # Callback from WorkQueue
    def reportIdle(self, idle):
        if self.idle == idle:
            return
        self.idle = idle

        if self.idle:
            self.logger.log("Warning: work queue empty, miner is idle")
            self.logger.dispatch(RateUpdateLog(0))
            self.setMeta('rate', 0)

    @defer.inlineCallbacks
    def attemptFailback(self):
        backendURL = self.config.get('general', 'backend', str, '')
        ok = yield backend.testURL(backendURL)
        if not self.failbackLoop:
            return
        if ok:
            self.logger.log('Primary backend is available, switching back...')
            self.switchURL(backendURL)

    # Connection callback handlers
    def onFailure(self):
        backups = self.config.get('general', 'backups', str, '').split()
        backups.insert(0, self.config.get('general', 'backend', str, ''))
        try:
            index = backups.index(self.connectionURL)
        except ValueError:
            index = -1
        nextIndex = (index+1)%len(backups)
        nextBackend = backups[nextIndex]
        #Important! Stratum doesn't have a retry rate polling thingy
        if nextBackend == self.connectionURL and not isinstance(self.connection, StratumClient):
            self.logger.log("Couldn't connect to server, retrying...")
        else:
            wait = self.config.get('general', 'retry_rate', int, 1)
            self.logger.log("Couldn't connect to server, switching backend in %ds..." % wait)
            time.sleep(wait)
            self.switchURL(nextBackend)
            if nextIndex != 0:
                #assert not self.failbackLoop
                failbackInterval = self.config.get('general', 'failback',
                                                   int, 600)
                if failbackInterval and not self.failbackLoop:
                    self.failbackLoop = task.LoopingCall(self.attemptFailback)
                    self.failbackLoop.start(failbackInterval)
    
    def onSwitchserver(self, url):
        self.switchURL(url)
    def onConnect(self):
        if not self.connected:
            self.logger.dispatch(ConnectionLog(True, self.connectionURL))
            self.connected = True
            self.logger.refreshStatus()
    def onDisconnect(self):
        if self.connected:
            self.logger.dispatch(ConnectionLog(False, self.connectionURL))
            self.connected = False
            self.logger.refreshStatus()
    def onBlock(self, block):
        self.logger.dispatch(BlockChangeLog(block))
    def onMsg(self, msg):
        self.logger.log('MSG: ' + str(msg))
    def onWork(self, work):
        self.logger.debug('We have new work; passing to WorkQueue')
        self.queue.storeWork(work)
    def onWorkclear(self):
        self.logger.debug('Server asked to clear work queue')
        self.queue.workClear()
    def onLongpoll(self, lp):
        self.connectionType = 'rpclp' if lp else 'rpc'
        self.logger.refreshStatus()
    def onPush(self, ignored):
        self.logger.dispatch(LongPollPushLog())
    def onLog(self, message):
        self.logger.log(message)
    def onDebug(self, message):
        self.logger.debug(message)
