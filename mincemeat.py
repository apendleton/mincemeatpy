#!/usr/bin/env python


################################################################################
# Copyright (c) 2010 Michael Fairley
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
################################################################################

import asynchat
import asyncore
import cPickle as pickle
import hashlib
import hmac
import logging
import marshal
import optparse
import os
import random
import socket
import sys
import types
import sqlite3
import itertools
import json
import collections

VERSION = "0.1.2"


DEFAULT_PORT = 11235



class Protocol(asynchat.async_chat):
    def __init__(self, conn=None):
        if conn:
            asynchat.async_chat.__init__(self, conn)
        else:
            asynchat.async_chat.__init__(self)

        self.set_terminator("\n")
        self.buffer = []
        self.auth = None
        self.mid_command = False

    def collect_incoming_data(self, data):
        self.buffer.append(data)

    def send_command(self, command, data=None):
        if not ":" in command:
            command += ":"
        if data:
            pdata = pickle.dumps(data)
            command += str(len(pdata))
            logging.debug( "<- %s" % command)
            self.push(command + "\n" + pdata)
        else:
            logging.debug( "<- %s" % command)
            self.push(command + "\n")

    def found_terminator(self):
        if not self.auth == "Done":
            command, data = (''.join(self.buffer).split(":",1))
            self.process_unauthed_command(command, data)
        elif not self.mid_command:
            logging.debug("-> %s" % ''.join(self.buffer))
            command, length = (''.join(self.buffer)).split(":", 1)
            if command == "challenge":
                self.process_command(command, length)
            elif length:
                self.set_terminator(int(length))
                self.mid_command = command
            else:
                self.process_command(command)
        else: # Read the data segment from the previous command
            if not self.auth == "Done":
                logging.fatal("Recieved pickled data from unauthed source")
                sys.exit(1)
            data = pickle.loads(''.join(self.buffer))
            self.set_terminator("\n")
            command = self.mid_command
            self.mid_command = None
            self.process_command(command, data)
        self.buffer = []

    def send_challenge(self):
        self.auth = os.urandom(20).encode("hex")
        self.send_command(":".join(["challenge", self.auth]))

    def respond_to_challenge(self, command, data):
        mac = hmac.new(self.password, data, hashlib.sha1)
        self.send_command(":".join(["auth", mac.digest().encode("hex")]))
        self.post_auth_init()

    def verify_auth(self, command, data):
        mac = hmac.new(self.password, self.auth, hashlib.sha1)
        if data == mac.digest().encode("hex"):
            self.auth = "Done"
            logging.info("Authenticated other end")
        else:
            self.handle_close()

    def process_command(self, command, data=None):
        commands = {
            'challenge': self.respond_to_challenge,
            'disconnect': lambda x, y: self.handle_close(),
            }

        if command in commands:
            commands[command](command, data)
        else:
            logging.critical("Unknown command received: %s" % (command,)) 
            self.handle_close()

    def process_unauthed_command(self, command, data=None):
        commands = {
            'challenge': self.respond_to_challenge,
            'auth': self.verify_auth,
            'disconnect': lambda x, y: self.handle_close(),
            }

        if command in commands:
            commands[command](command, data)
        else:
            logging.critical("Unknown unauthed command received: %s" % (command,)) 
            self.handle_close()
        

class Client(Protocol):
    def __init__(self):
        Protocol.__init__(self)
        self.mapfn = self.reducefn = self.collectfn = None
        
    def conn(self, server, port):
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connect((server, port))
        asyncore.loop()

    def handle_connect(self):
        pass

    def handle_close(self):
        self.close()

    def set_mapfn(self, command, mapfn):
        self.mapfn = types.FunctionType(marshal.loads(mapfn), globals(), 'mapfn')

    def set_collectfn(self, command, collectfn):
        self.collectfn = types.FunctionType(marshal.loads(collectfn), globals(), 'collectfn')

    def set_reducefn(self, command, reducefn):
        self.reducefn = types.FunctionType(marshal.loads(reducefn), globals(), 'reducefn')

    def call_mapfn(self, command, data):
        logging.info("Mapping %s" % str(data[0]))
        results = {}
        for k, v in self.mapfn(data[0], data[1]):
            if k not in results:
                results[k] = []
            results[k].append(v)
        if self.collectfn:
            for k in results:
                results[k] = [self.collectfn(k, results[k])]
        self.send_command('mapdone', (data[0], results))

    def call_reducefn(self, command, data):
        logging.info("Reducing %s" % str(data[0]))
        results = self.reducefn(data[0], data[1])
        self.send_command('reducedone', (data[0], results))

    def call_reducefn_partial(self, command, data):
        logging.info("Reducing partial %s" % str(data[0]))
        results = self.reducefn(data[0][0], data[1])
        self.send_command('reducedone', (data[0], results))
        
    def process_command(self, command, data=None):
        commands = {
            'mapfn': self.set_mapfn,
            'collectfn': self.set_collectfn,
            'reducefn': self.set_reducefn,
            'map': self.call_mapfn,
            'reduce': self.call_reducefn,
            'partialreduce': self.call_reducefn_partial
            }

        if command in commands:
            commands[command](command, data)
        else:
            Protocol.process_command(self, command, data)

    def post_auth_init(self):
        if not self.auth:
            self.send_challenge()

    def handle_error(self):
        t, v, tb = sys.exc_info()
        if t == socket.error:
            raise v
        else:
            Protocol.handle_error(self)


class TaskManager(object):
    START = 0
    MAPPING = 1
    REDUCING = 2
    FINISHED = 3

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        self._state = new_state

    def __init__(self, datasource, server):
        self.datasource = datasource
        self.server = server
        self.state = TaskManager.START

    def next_task(self, channel):
        if self.state == TaskManager.START:
            self.map_iter = iter(self.datasource)
            self.working_maps = {}
            self.map_results = {}
            #self.waiting_for_maps = []
            self.state = TaskManager.MAPPING
        if self.state == TaskManager.MAPPING:
            try:
                map_key = self.map_iter.next()
                map_item = map_key, self.datasource[map_key]
                self.working_maps[map_item[0]] = map_item[1]
                return ('map', map_item)
            except StopIteration:
                if len(self.working_maps) > 0:
                    key = random.choice(self.working_maps.keys())
                    return ('map', (key, self.working_maps[key]))
                self.state = TaskManager.REDUCING
                self.reduce_iter = self.get_reduce_iter()
                self.working_reduces = {}
                self.results = {}
                return self.next_task(channel)
        if self.state == TaskManager.REDUCING:
            try:
                reduce_item = self.reduce_iter.next()
                self.working_reduces[reduce_item[0]] = reduce_item[1]
                return ('reduce', reduce_item)
            except StopIteration:
                if len(self.working_reduces) > 0:
                    key = random.choice(self.working_reduces.keys())
                    return ('reduce', (key, self.working_reduces[key]))
                self.state = TaskManager.FINISHED
        if self.state == TaskManager.FINISHED:
            self.server.handle_close()
            return ('disconnect', None)
    
    def map_done(self, data):
        # Don't use the results if they've already been counted
        if not data[0] in self.working_maps:
            return

        self.save_map_results(data[0], data[1].iteritems())
        del self.working_maps[data[0]]

    def save_map_results(self, key, results):
        for (key, values) in results:
            if key not in self.map_results:
                self.map_results[key] = []
            self.map_results[key].extend(values)

    def get_reduce_iter(self):
        return self.map_results.iteritems()
                                
    def reduce_done(self, data):
        # Don't use the results if they've already been counted
        if not data[0] in self.working_reduces:
            return

        self.save_reduce_results(data[0], data[1])
        del self.working_reduces[data[0]]

    def save_reduce_results(self, key, result):
        self.results[key] = result

    def get_results(self):
        return self.results.iteritems()

class Server(asyncore.dispatcher, object):
    taskmanager_cls = TaskManager

    def __init__(self):
        asyncore.dispatcher.__init__(self)
        self.mapfn = None
        self.reducefn = None
        self.collectfn = None
        self.datasource = None
        self.password = None

    def run_server(self, password="", port=DEFAULT_PORT):
        self.password = password
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bind(("", port))
        self.listen(1)
        try:
            asyncore.loop()
        except:
            self.close_all()
            raise
        
        return self.taskmanager.get_results()

    def handle_accept(self):
        conn, addr = self.accept()
        sc = ServerChannel(conn, self)
        sc.password = self.password

    def handle_close(self):
        self.close()

    def set_datasource(self, ds):
        self._datasource = ds
        self.taskmanager = self.taskmanager_cls(self._datasource, self)
    
    def get_datasource(self):
        return self._datasource

    datasource = property(get_datasource, set_datasource)


class ServerChannel(Protocol):
    def __init__(self, conn, server):
        Protocol.__init__(self, conn)
        self.server = server

        self.start_auth()

    def handle_close(self):
        logging.info("Client disconnected")
        self.close()

    def start_auth(self):
        self.send_challenge()

    def start_new_task(self):
        command, data = self.server.taskmanager.next_task(self)
        if command == None:
            return
        self.send_command(command, data)

    def map_done(self, command, data):
        self.server.taskmanager.map_done(data)
        self.start_new_task()

    def reduce_done(self, command, data):
        self.server.taskmanager.reduce_done(data)
        self.start_new_task()

    def process_command(self, command, data=None):
        commands = {
            'mapdone': self.map_done,
            'reducedone': self.reduce_done,
            }

        if command in commands:
            commands[command](command, data)
        else:
            Protocol.process_command(self, command, data)

    def post_auth_init(self):
        if self.server.mapfn:
            self.send_command('mapfn', marshal.dumps(self.server.mapfn.func_code))
        if self.server.reducefn:
            self.send_command('reducefn', marshal.dumps(self.server.reducefn.func_code))
        if self.server.collectfn:
            self.send_command('collectfn', marshal.dumps(self.server.collectfn.func_code))
        self.start_new_task()

class SqliteTaskManager(TaskManager):
    INITIAL_SQL = "initial.sql"

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        self.cursor.execute("update state set current_state = :state", (new_state,))
        self._state = new_state

    def __init__(self, datasource, server):
        self.db = server.db
        
        if not server.resume:
            # load initial schema
            self.cursor = self.db.cursor()

            schema = open(os.path.join(os.path.dirname(__file__), self.INITIAL_SQL)).read()
            self.cursor.executescript(schema)

            super(SqliteTaskManager, self).__init__(datasource, server)
        else:
            self.cursor = self.db.cursor()
            
            super(SqliteTaskManager, self).__init__(datasource, server)
            
            old_state = list(self.cursor.execute("select * from state"))
            if old_state:
                self._state = old_state[0][0]
            else:
                raise Exception("No state found; resumption failed.")

    def save_map_results(self, mkey, results):
        for (rkey, values) in results:
            json_key = json.dumps(rkey)
            for value in values:
                self.cursor.execute("insert into map_results (key, value) values (:key, :data)", (json_key, sqlite3.Binary(pickle.dumps(value, -1))))
   
    def _get_reduce_results(self):
        self.db.commit()

        # use a dedicated cursor for this so it doesn't get trampled by reduce saves
        cursor = self.db.cursor()
        return cursor.execute("select key, value from map_results order by key asc")

    def get_reduce_iter(self):
        map_results = self._get_reduce_results()
        groups = itertools.groupby(
            itertools.imap(
                lambda row: (tuple(json.loads(row[0])), pickle.loads(str(row[1]))),
                map_results
            ),
            key = lambda d: d[0]
        )
        return itertools.imap(lambda group: (group[0], map(lambda record: record[1], group[1])), groups)

    def save_reduce_results(self, rkey, result):
        json_key = json.dumps(rkey)
        self.cursor.execute("insert into reduce_results (key, value) values (:key, :data)", (json_key, sqlite3.Binary(pickle.dumps(result, -1))))

    def get_results(self):
        self.db.commit()

        cursor = self.db.cursor()
        reduce_results = cursor.execute("select key, value from reduce_results order by key asc")
        return itertools.imap(
            lambda row: (tuple(json.loads(row[0])), pickle.loads(str(row[1]))),
            reduce_results
        )

class BatchSqliteTaskManager(SqliteTaskManager):
    INITIAL_SQL = "initial_batch.sql"

    def __init__(self, datasource, server):
        super(BatchSqliteTaskManager, self).__init__(datasource, server)
        self.multiple_slices = set()
        self.depth = 0

    def save_map_results(self, mkey, results, depth=0):
        for (rkey, values) in results:
            json_key = json.dumps(rkey)
            for value in values:
                self.cursor.execute("insert into map_results (key, value, depth) values (:key, :data, :depth)", (json_key, sqlite3.Binary(pickle.dumps(value, -1)), depth))

    def _get_reduce_results(self):
        self.db.commit()

        # use a dedicated cursor for this so it doesn't get trampled by reduce saves
        cursor = self.db.cursor()
        return cursor.execute("select key, value from map_results where depth = :depth order by depth, key asc", (self.depth,))

    def get_reduce_iter(self):        
        def batched_iter():
            bare_iter = super(BatchSqliteTaskManager, self).get_reduce_iter()
            for key, records in bare_iter:
                hn_records = HNWrapper(records)
                slice_count = 0
                while True:
                    out = list(itertools.islice(hn_records, self.server.batch_size))
                    if out:
                        slice_count += 1

                        if hn_records.hasnext():
                            # this slice didn't consume the whole group, so flag it
                            self.multiple_slices.add(key)

                        yield (key, slice_count, self.depth), out
                    else:
                        break

        return batched_iter()

    def reduce_done(self, data):
        # Don't use the results if they've already been counted
        if not data[0] in self.working_reduces:
            return

        if data[0][0] in self.multiple_slices:
            self.save_map_results(data[0], [(data[0][0], [data[1]])], depth=self.depth+1)
        else:
            self.save_reduce_results(data[0][0], data[1])
        del self.working_reduces[data[0]]

    def next_task(self, channel):
        if self.state == TaskManager.REDUCING:
            try:
                reduce_item = self.reduce_iter.next()
                self.working_reduces[reduce_item[0]] = reduce_item[1]
                return ('partialreduce', reduce_item)
            except StopIteration:
                if len(self.working_reduces) > 0:
                    key = random.choice(self.working_reduces.keys())
                    return ('partialreduce', (key, self.working_reduces[key]))
                # we might actually be done, but we might also have to repeat the reduce round
                if self.multiple_slices:
                    # at least one key needs another reduction round
                    self.depth += 1
                    self.multiple_slices = set()
                    self.reduce_iter = self.get_reduce_iter()
                    return self.next_task(channel)
                else:
                    self.state = TaskManager.FINISHED
        return super(BatchSqliteTaskManager, self).next_task(channel)

class SqliteServer(Server):
    taskmanager_cls = SqliteTaskManager

    def __init__(self, db_path, resume=False):
        self.db = sqlite3.connect(db_path)
        self.resume = resume
        super(SqliteServer, self).__init__()

class BatchSqliteServer(SqliteServer):
    taskmanager_cls = BatchSqliteTaskManager

    def __init__(self, db_path, batch_size, resume=False):
        self.batch_size = batch_size
        super(BatchSqliteServer, self).__init__(db_path, resume)

# from http://stackoverflow.com/questions/1966591/hasnext-in-python-iterators
class HNWrapper(object):
    def __init__(self, it):
        self.it = iter(it)
        self._hasnext = None
    def __iter__(self): return self
    def next(self):
        if self._hasnext:
            result = self._thenext
        else:
            result = next(self.it)
        self._hasnext = None
        return result
    def hasnext(self):
        if self._hasnext is None:
            try: self._thenext = next(self.it)
            except StopIteration: self._hasnext = False
            else: self._hasnext = True
        return self._hasnext

def run_client():
    parser = optparse.OptionParser(usage="%prog [options]", version="%%prog %s"%VERSION)
    parser.add_option("-p", "--password", dest="password", default="", help="password")
    parser.add_option("-P", "--port", dest="port", type="int", default=DEFAULT_PORT, help="port")
    parser.add_option("-v", "--verbose", dest="verbose", action="store_true")
    parser.add_option("-V", "--loud", dest="loud", action="store_true")

    (options, args) = parser.parse_args()
                      
    if options.verbose:
        logging.basicConfig(level=logging.INFO)
    if options.loud:
        logging.basicConfig(level=logging.DEBUG)

    client = Client()
    client.password = options.password
    client.conn(args[0], options.port)
                      

if __name__ == '__main__':
    run_client()
