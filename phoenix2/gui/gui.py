import pygtk
pygtk.require("2.0")
import gtk
import threading
from twisted.internet import reactor

#TODO Systray right-click buggy... fix!
#TODO check size limit of TextView!

class GUI(object):
    def exit(self, widget, data=None):
        self.statusicon.set_visible(False)
        gtk.main_quit()
        reactor.callFromThread(reactor.stop)
        
    def statusicon_menu(self, icon, button, time):
        menu = gtk.Menu()

        quit = gtk.MenuItem("Quit")
        quit.connect("activate", self.exit)

        menu.append(quit)
        menu.show_all()

        menu.popup(None, None, None, button, time, self.statusicon)
        
    def window_state_event(self, widget, event):
        if event.changed_mask & gtk.gdk.WINDOW_STATE_ICONIFIED:
            if event.new_window_state & gtk.gdk.WINDOW_STATE_ICONIFIED:
                self.win.hide()
        return True
        
    def delete_event(self, window, event):
        #don't delete; hide instead
        self.win.hide_on_delete()
        self.statusicon.set_visible(True)
        return True

    def status_clicked(self, status):
        if self.win.get_property("visible"):
            #hide
            self.win.hide()
        else:
            self.win.show_all()
            self.win.present()
    
    def scroll_to_bot(self, *widgets):
        for widget in widgets:
            adj = widget.parent.get_vadjustment()
            adj.set_value( adj.upper - adj.page_size )
        
    def treeview_auto_scroll(self, widget, event, data=None):
        """Autoscroll ONLY IF at (one-fifth? of) last page"""
        if not isinstance(widget.parent, gtk.ScrolledWindow): return
        
        adj = widget.parent.get_vadjustment()
        max = adj.upper - adj.page_size
        if max - adj.value <= adj.page_size/5:
            adj.set_value( max )
            
    def map_event(self, widget, event):
        self.scroll_to_bot(self.shareView, self.consoleView)
            
    def add_auto_scroll(self, *widgets):
        for widget in widgets:
            widget.connect("size-allocate", self.treeview_auto_scroll)
        
    def add_to_console(self, text):
        buffer = self.consoleView.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        
    def replace_status(self, text):
        statusbar = self.mainStatusbar
        description = "Status"
        context_id = statusbar.get_context_id(description)
        #remove previous
        statusbar.pop(context_id)
        statusbar.push(context_id, text)
        
    def add_share(self, time, device, hash, accepted, error):
        store = self.shareStore
        if accepted:
            status = gtk.STOCK_APPLY
        else:
            status = gtk.STOCK_CLOSE
        store.append([time, device, hash, status, error])
        
    def status_tooltip(self, text):
        if self.statusicon.get_visible():
            self.statusicon.set_tooltip_text(text)
        
    def __init__(self):
        self.builder = gtk.Builder()
        self.builder.add_from_file("phoenix2/gui/win.ui")
        try:
            self.icon = gtk.gdk.pixbuf_new_from_file("phoenix2/gui/phoenix-icon.png")
        except:
            self.icon = None
        
        self.win = self.builder.get_object("mainWindow")
        if self.icon:
            self.win.set_icon(self.icon)
        #self.win.show_all() hide on start
        self.consoleView = self.builder.get_object("consoleView")
        self.mainStatusbar = self.builder.get_object("mainStatusbar")
        self.shareView = self.builder.get_object("shareView")
        self.shareStore = self.builder.get_object("shareStore")
        
        self.add_auto_scroll(self.shareView, self.consoleView)
        
        def addTextColumn(title, pos):
            col = gtk.TreeViewColumn(title + " ")
            self.shareView.append_column(col)
            cell = gtk.CellRendererText()
            col.pack_start(cell, False)
            col.add_attribute(cell, "text", pos)
            
        def addStockColumn(title, pos):
            col = gtk.TreeViewColumn(title + " ")
            self.shareView.append_column(col)
            cell = gtk.CellRendererPixbuf()
            col.pack_start(cell, False)
            col.add_attribute(cell, "stock-id", pos)
        
        addTextColumn("Time", 0)
        addTextColumn("Device", 1)
        addTextColumn("Hash", 2)
        addStockColumn("Status", 3)
        addTextColumn("Error", 4)
        
        self.statusicon = gtk.StatusIcon()
        if self.icon:
            self.statusicon.set_from_pixbuf(self.icon)
        else:
            self.statusicon.set_from_stock(gtk.STOCK_HOME)
        self.statusicon.connect("popup-menu", self.statusicon_menu)
        self.statusicon.connect("activate", self.status_clicked)
        self.win.connect("delete-event", self.exit)
        self.win.connect("window-state-event", self.window_state_event)
        self.win.connect_after("map-event", self.map_event)
        self.statusicon.set_tooltip("Phoenix Miner") #TODO non-hardcode

class GUIThread(threading.Thread):
    def __init__(self, parent):
        threading.Thread.__init__(self)
        self.parent = parent
        
    def run(self):
        self.parent.gui = self.gui = GUI()
        gtk.main()



