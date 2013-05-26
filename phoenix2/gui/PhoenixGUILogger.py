from ..core.PhoenixLogger import PhoenixLogger, ConsoleOutput, ResultLog
import gobject
import gui
import time
import sys


class GUIOutput(ConsoleOutput):
    """Start the GUI thread here."""
    def __init__(self):
        #we don't need a super
        gobject.threads_init()
        
        self.gui = None
        self.t = gui.GUIThread(self)
        self.t.start()
        
        while not self.gui:
            pass #wait
        #VERIFY block until GUI is started?! Else there will be race conditions.
        
        #TODO need to stop thread when exit??? or when gtk main loop exits, thread stops?
    
    def checkGUI(self):
        if not self.t.is_alive():
            pass
            #gui thread stopped
             # we need to use callLater because this object is created before reactor is run
    
    def add_share(self, time, device, hash, accepted, error):
        #if not self.gui: return
        self.checkGUI()
        gobject.idle_add(self.gui.add_share, time, device, hash, accepted, error)
        
    #Override methods
    def status(self, status):
        #if not self.gui: return
        self.checkGUI()
        gobject.idle_add(self.gui.replace_status, status)
        gobject.idle_add(self.gui.status_tooltip, status)

    def printline(self, line):
        #if not self.gui: return
        self.checkGUI()
        gobject.idle_add(self.gui.add_to_console, line + "\n")

class PhoenixGUILogger(PhoenixLogger):
    """Might need to override result log dispatch?"""
    
    def __init__(self, core):
        self.console = GUIOutput()
        self.core = core
        self.rateText = '0 Khash/s'

        self.accepted = 0
        self.rejected = 0

        self.consoleDay = None

        self.logfile = None
        self.logfileName = None

        self.rpcLogs = []
        self.rpcIndex = 0

        self.nextRefresh = 0
        self.refreshScheduled = False
        self.refreshStatus()
        
    def dispatch(self, log):
        super(PhoenixGUILogger, self).dispatch(log)
        if isinstance(log, ResultLog):
            hash_str = log.hash[::-1].encode('hex')
            error = "%s: %s" %(log.error_code, log.error_msg) if log.error_code or log.error_msg else None
            time_str = '%s' % time.strftime('%H:%M:%S', time.localtime(log.time))
            device_name = log.kernelif.getName() if log.kernelif else None
            self.console.add_share(time_str, device_name, hash_str, log.accepted, error)
        