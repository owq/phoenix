# Copyright (C) 2011 by jedi95 <jedi95@gmail.com> and
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

from ClientBase import ClientBase, AssignedWork

#poclbm StratumSource
from binascii import hexlify, unhexlify
from hashlib import sha256
from json import dumps, loads
from struct import pack, unpack
from threading import Thread, Lock, Timer
from time import time
import asynchat
import asyncore
import socket
import traceback
from twisted.internet import reactor

class Job(object):
    def __init__(self):
        self.job_id = None
        self.prevhash = None
        self.coinbase1 = None
        self.coinbase2 = None
        self.merkle_branch = None
        self.version = None
        self.nbits = None
        self.ntime = None
        self.ntime_delta = None
        self.extranonce2 = None
        
    def build_merkle_root(self, coinbase_hash):
        merkle_root = coinbase_hash
        for h in self.merkle_branch:
            merkle_root = doublesha(merkle_root + unhexlify(h))
        return merkle_root
    
    def serialize_header(self, merkle_root, ntime, nonce):
        r =  self.version
        r += self.prevhash
        r += merkle_root
        r += hexlify(pack(">I", ntime))
        r += self.nbits
        r += hexlify(pack(">I", nonce))
        #VERIFY padding may not be needed
        #r += '000000800000000000000000000000000000000000000000000000000000000000000000000000000000000080020000' # padding
        return r

def chunks(l, n):
    for i in xrange(0, len(l), n):
        yield l[i:i+n]
        
def doublesha(b):
    return sha256(sha256(b).digest()).digest()

def reverse_hash(h):
    return pack('>IIIIIIII', *unpack('>IIIIIIII', h)[::-1])[::-1]

class StratumClient(ClientBase):
    """The actual root of the whole Stratum(RPC) client system."""

    def __init__(self, handler, url):
        self.handler = handler #this handler is core, not the async handler
        self.url = url #NOTE: this url is already parsed!!
        self.params = {}
        for param in url.params.split('&'):
            s = param.split('=',1)
            if len(s) == 2:
                self.params[s[0]] = s[1]
        #self.auth = 'Basic ' + ('%s:%s' % (
        #    url.username, url.password)).encode('base64').strip()
        #Stratum doesn't need Auth header.
        self.version = 'RPCClient/2.0'

        self.saidConnected = False
        
        #stratum source
        #TODO can remove some variables... not used here
        self.BASE_DIFFICULTY = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
        self.socket_handler = None
        self.socket = None
        self.channel_map = {}
        self.subscribed = False
        self.authorized = None
        self.submits = {}
        self.last_submits_cleanup = time()
        self.server_difficulty = self.BASE_DIFFICULTY #TODO does phoenix have a difficulty of its own?
        self.jobs = {}
        self.current_job = None
        self.extranonce = ''
        self.extranonce2_size = 4
        self.send_lock = Lock()
        self.send_callback = None
        self.message_timeout = 15
        self.maxtime = self.handler.config.get('general', 'maxtime', int, 88) if self.handler.config else 88
        self.firstDifficultySet = True
        
    ## From poclbm StratumSource ##
    def send_message(self, message):
        data = dumps(message) + '\n'
        try:
            #self.socket_handler.push(data)
            #there is some bug with asyncore's send mechanism http://bugs.python.org/issue17925
            #so we send data 'manually'
            #note that this is not thread safe
            #Need to test this.
            with self.send_lock:
                if not self.handler:
                    return False
                while data:
                    sent = self.handler.send(data)
                    data = data[sent:]
                return True

        except AttributeError:
            self.stop()
        except Exception:
            self.stop()
                          
    def handle_message(self, message):
        #TODO CONSUME IF DISCONNECT
        
        #Miner API
        if 'method' in message:

            #mining.notify
            if message['method'] == 'mining.notify':
                params = message['params']

                j = Job()

                j.job_id = params[0]
                j.prevhash = params[1]
                j.coinbase1 = params[2]
                j.coinbase2 = params[3]
                j.merkle_branch = params[4]
                j.version = params[5]
                j.nbits = params[6]
                j.ntime = params[7]
                j.ntime_delta = int(j.ntime, 16) - int(time())
                clear_jobs = params[8]
                if clear_jobs:
                    self.jobs.clear()
                    self.runCallback('workclear')
                j.extranonce2 = 0
                
                self.runCallback('debug', "Received new job %s" % j.job_id)
                self.jobs[j.job_id] = j
                self.current_job = j

                self.handleWork(self.stratum_to_getwork(j))

            #mining.get_version
            if message['method'] == 'mining.get_version':
                self.send_message({"error": None, "id": message['id'], "result": self.user_agent})

            #mining.set_difficulty
            #VERIFY REFRESH TARGETS/clear work queue on FIRST difficulty change???
            elif message['method'] == 'mining.set_difficulty':
                self.runCallback('debug', "Setting new difficulty: %s" % message['params'][0])
                self.server_difficulty = self.BASE_DIFFICULTY / message['params'][0]
                if self.firstDifficultySet:
                    if self.server_difficulty != self.BASE_DIFFICULTY:
                        self.runCallback('workclear')
                        self.requestWork()
                    self.firstDifficultySet = False
                
            # i guess we leave the next 2 for later... not that easy to implement
            # need to "switchURL"

            #TODO verify this works
            elif message['method'] == 'client.reconnect':
                address, port = self.url.hostname, self.url.port
                (new_address, new_port, timeout) = message['params'][:3]
                if new_address: address = new_address
                if new_port != None: port = new_port
                self.runCallback('debug', "%s asked us to reconnect to %s:%d in %d seconds" % (self.url.hostname, address, port, timeout))
                #self.server().host = address + ':' + str(port)
                url = "stratum://%s:%s@%s:%d" % (self.url.username, self.url.password, address, port)
                def reconnect():
                    self.runCallback('switchserver', url)
                Timer(timeout, reconnect).start()

            #client.add_peers TODO deal with this later (damn troublesome)
            elif message['method'] == 'client.add_peers':
                hosts = [{'host': host[0], 'port': host[1]} for host in message['params'][0]]
                #self.switch.add_servers(hosts)

        #responses to server API requests
        elif 'result' in message:

            #check if this is submit confirmation (message id should be in submits dictionary)
            #cleanup if necessary
            #TODO this part
            if message['id'] in self.submits:
                nonce = self.submits[message['id']][:1]
                accepted = message['result'] #this is the response BODY
                
                if 'error' in message and message['error']:
                    self.runCallback('debug', 'Error %d: %s' % (message['error'][0], message['error'][1]))
                    self.send_callback(accepted, message['error'][0], message['error'][1])
                else:
                    self.send_callback(accepted)
                del self.submits[message['id']]
                if time() - self.last_submits_cleanup > 3600:
                    now = time()
                    for key, value in self.submits.items():
                        if now - value[2] > 3600:
                            del self.submits[key]
                    self.last_submits_cleanup = now
                    
            #response to mining.subscribe
            #store extranonce and extranonce2_size
            elif message['id'] == 's':
                if self.sub_timer: 
                    self.sub_timer.cancel()
                    
                self.extranonce = message['result'][1]
                self.extranonce2_size = message['result'][2]
                self.extranonce2_max = 256 ** self.extranonce2_size - 1
                self.subscribed = True
                self.runCallback('debug', 'Subscribed to server. Extranonce: %s' % self.extranonce)
                self.authorize(self.message_timeout)

            #response to mining.authorize
            elif message['id'] == self.url.username:
                if self.auth_timer:
                    self.auth_timer.cancel() 
                if not message['result']:
                    self.runCallback('debug', 'authorization failed with %s:%s@%s' %(self.url.username, self.url.password, self.url.hostname))
                    self.authorized = False
                    self.stop()
                else:
                    self.runCallback('debug', 'We are authorized and ready for action. B)')
                    self.authorized = True
                    if not self.saidConnected:
                        self.saidConnected = True
                        self.runCallback('connect')
                    
            else:
                pass #TODO handle unknown cases?
                
    def extranonce2_padding(self, extranonce2):
        '''Return extranonce2 with padding bytes'''

        if not self.extranonce2_size:
            raise Exception("Extranonce2_size isn't set yet")

        extranonce2_bin = pack('>I', extranonce2)
        missing_len = self.extranonce2_size - len(extranonce2_bin)

        if missing_len < 0:
            # extranonce2 is too long, we should print warning on console,
            # but try to shorten extranonce2
            self.runCallback('debug', "Extranonce size mismatch. Please report this error to pool operator!")
            return extranonce2_bin[abs(missing_len):]

        # This is probably more common situation, but it is perfectly
        # safe to add whitespaces
        return '\x00' * missing_len + extranonce2_bin
        
    def stratum_to_getwork(self, j):
        """Prepare stratum job for hashing. From stratum-mining-proxy"""

        # 1. Increase extranonce2
        # TODO what if it overflows? need to store some jobs.
        j.extranonce2 += 1
        
        # 2. Build final extranonce
        extranonce1_bin = unhexlify(self.extranonce)
        extranonce2_bin = self.extranonce2_padding(j.extranonce2)
        extranonce = extranonce1_bin + extranonce2_bin

        # 3. Put coinbase transaction together
        coinbase1_bin = unhexlify(j.coinbase1)
        coinbase2_bin = unhexlify(j.coinbase2)
        coinbase_bin = coinbase1_bin + extranonce + coinbase2_bin

        # 4. Calculate coinbase hash
        coinbase_hash = doublesha(coinbase_bin)

        # 5. Calculate merkle root
        merkle_root = hexlify(reverse_hash(j.build_merkle_root(coinbase_hash)))

        # 6. Generate current ntime
        ntime = int(time()) + j.ntime_delta #Might need to use fixed time to test.

        # 7. Serialize header
        j.block_header = j.serialize_header(merkle_root, ntime, 0)
        
        work = {}
        work['data'] = j.block_header
        work['target'] = ''.join(list(chunks('%064x' % self.server_difficulty, 2))[::-1])

        return (work, hexlify(extranonce2_bin), j.job_id)
    
    #TODO need to test if switching backends automatically still WORKS!
    def stop(self):
        #raise Exception
        self._failure()
        
    def asyncore_thread(self):
        """Connection Handler event loop"""
        asyncore.loop(map=self.channel_map)
        
    def subscribe(self, timeout):       
        self.send_message({'id': 's', 'method': 'mining.subscribe', 'params': []})
        def subscribe_timeout():
            if not self.subscribed:
                self.runCallback('debug', 'Failed to subscribe')
                self.stop()
        self.sub_timer = Timer(timeout, subscribe_timeout)
        self.sub_timer.start()
        #We shouldn't need to wait since we can assume jobs will come only after we confirm subscription

    def authorize(self, timeout):
        self.runCallback('debug', 'Trying to authorize %s:%s' % (self.url.username, self.url.password))
        self.send_message({'id': self.url.username, 'method': 'mining.authorize', 'params': [self.url.username, self.url.password]})
        def authorize_timeout():
            if not self.authorized:
                self.runCallback('debug', 'Failed to authorize')
                self.stop()
        self.auth_timer = Timer(timeout, authorize_timeout)
        self.auth_timer.start()
        #TODO VERIFY can we assume that server will only send jobs ONLY AFTER we are authorized?
    
    ## Phoenix standard API ##
    def connect(self):
        """NEW subscription to server"""
        #note that self.url is already parsed!
        
        try:
            self.runCallback('debug', 'Trying to connect to %s:%s' % (self.url.hostname, self.url.port))
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.url.hostname, self.url.port))
                  
            if not self.socket_handler:
                self.socket_handler = Handler(self.socket, self.channel_map, self) #handler added to map
                thread = Thread(target=self.asyncore_thread)
                thread.daemon = True
                thread.start()
                #TODO do we need to stop the thread when disconnect? or will it stop when asyncore.loop closes?
    
            self.subscribe(self.message_timeout) #when subscribed, authorize() will be called                
                
        except socket.error:
                self.runCallback('debug', 'Socket error')
                self.stop()

    def disconnect(self):
        """Cease server communications immediately. The client is probably not
        reusable, so it's probably best not to try.
        """
        
        #Usually called from another class
        #CLEANUP
        
        self._deactivateCallbacks()
        
        if self.socket_handler:
            self.socket_handler.close() #thread should stop when done?

    def setMeta(self, var, value):
        """RPC clients do not support meta. Ignore."""

    def setVersion(self, shortname, longname=None, version=None, author=None):
        if version is not None:
            self.version = '%s/%s' % (shortname, version)
        else:
            self.version = shortname
            
    def switchCurrentJob(self):
        """Delete current job and get a new one. """
        if self.current_job.job_id in self.jobs:
            del self.jobs[self.current_job.job_id]
            if len(self.jobs) > 0:
                for id, job in self.jobs:
                    self.current_job = job
                    break
            else:
                self.current_job = None
                return

    def requestWork(self):
        """Application needs work right now. Refresh job, also checking if job extranonce2 is max."""
        #VERIFY refresh the job and also check that we only need to check extranonce2 max here.
        if self.current_job:
            if self.current_job.extranonce2 >= self.extranonce2_max:
                #Expire job and get new one
                self.runCallback('debug', "Job %s reached extranonce2 limit at %X, removing..." % (self.current_job.job_id, self.current_job.extranonce2))    
                self.switchCurrentJob()
                if not self.current_job:
                    return
                    
            self.runCallback('debug', "Refreshing job %s; extranonce2: %X" % (self.current_job.job_id, self.current_job.extranonce2))        
            #VERIFY that recursion is fixed here
            reactor.callLater(0, self.handleWork, self.stratum_to_getwork(self.current_job))

    def sendResult(self, wu, nonce, timestamp, callback):
        """Send share to server. Adapted from poclbm"""
        self.send_callback = callback
        job_id = wu.job_id
        extranonce2 = wu.extranonce2 #is this hex?
        
        ntime = pack('>I', timestamp).encode('hex') #NOTE: must be big endian!
        hex_nonce = pack('<I', nonce).encode('hex')
        id_ = job_id + hex_nonce
        self.submits[id_] = (nonce, time()) #'id': id_,
        return self.send_message({'params': [self.url.username, job_id, extranonce2, ntime, hex_nonce], 'id': id_, 'method': u'mining.submit'})

    def handleWork(self, (work, extranonce2, job_id), pushed=False):
        if work is None:
            return;

        aw = AssignedWork()
        aw.data = work['data'].decode('hex')[:80] #should be 80 anyhow, but we leave it
        aw.target = work['target'].decode('hex')
        aw.mask = work.get('mask', 32) #TODO direct
        aw.setMaxTimeIncrement(self.maxtime) #This might be really important, somehow... Figure out the best setting!
        aw.identifier = work.get('identifier', aw.data[4:36]) #Wait what is this??
        aw.extranonce2 = extranonce2
        aw.job_id = job_id
        
        if pushed:
            self.runCallback('push', aw)
        self.runCallback('work', aw)

    def _failure(self):
        if self.saidConnected:
            self.saidConnected = False
            self.runCallback('disconnect')
            if self.socket_handler:
                self.socket_handler.close_when_done() #thread should stop when done?
            self.connect() #Try once more
        else:
            self.runCallback('failure')
        
class Handler(asynchat.async_chat):
    def __init__(self, socket, map_, parent):
        asynchat.async_chat.__init__(self, socket, map_)
        self.parent = parent
        self.data = ''
        self.set_terminator('\n')

    def handle_close(self):
        self.close()
        self.parent.socket_handler = None
        self.parent.socket = None
    
    def handle_error(self):
        """Print traceback and stop client"""
        self.parent.runCallback('debug', traceback.format_exc())
        self.parent.stop()

    def collect_incoming_data(self, data):
        self.data += data

    def found_terminator(self):
        message = loads(self.data) #json load
        self.parent.handle_message(message)
        self.data = ''
