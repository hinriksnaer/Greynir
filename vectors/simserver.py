#!/usr/bin/env/python

"""
    Reynir: Natural language processing for Icelandic

    Similarity query server

    Copyright (C) 2017 Vilhjálmur Þorsteinsson

       This program is free software: you can redistribute it and/or modify
       it under the terms of the GNU General Public License as published by
       the Free Software Foundation, either version 3 of the License, or
       (at your option) any later version.
       This program is distributed in the hope that it will be useful,
       but WITHOUT ANY WARRANTY; without even the implied warranty of
       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
       GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see http://www.gnu.org/licenses/.


    This module implements a similarity query server. The server can
    answer queries about articles that are similar to a given article
    or topic vector. This assumes that articles already have topic vectors
    that are stored in the topic_vector column in the articles database table.

    The similarity server by default accepts TCP connections on port 5001.
    For security, this port should be closed from outside access via iptables or
    a firewall. However, the server also requires the client to authenticate
    using a secret key that is loaded from resources/SimilarityServerKey.txt.
    This file should not be made visible outside the Reynir server (or local
    network).

    To register this program as a service within systemd, create a unit file
    called similarity.service in the /etc/systemd/system directory, containing
    somthing like the following:

        [Unit]
        Description=Greynir document similarity service
        After=postgresql.service
        Before=greynir.service

        [Service]
        Type=simple
        User=[YOUR USERNAME]
        Group=[YOUR GROUPNAME]
        WorkingDirectory=/home/[YOUR USERNAME]/github/Reynir/vectors
        ExecStart=/home/[YOUR USERNAME]/github/Reynir/vectors/[YOUR VENV]/bin/python simserver.py
        Environment="PATH=/home/[YOUR USERNAME]/github/Reynir/vectors/[YOUR VENV]/bin"
        Environment="PYTHONIOENCODING=utf-8"
        Environment="PYTHONUNBUFFERED=True"
        StandardOutput=syslog
        StandardError=syslog

        [Install]
        WantedBy=multi-user.target

    Then run:
        $ sudo systemctl enable similarity
        $ sudo systemctl start similarity

"""

import json
import time
import math
import heapq
import sys
import operator

import numpy as np

from threading import Thread, Lock
from datetime import datetime
from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener, Client

from settings import Settings, ConfigError
from scraperdb import SessionContext, desc, Article, Root
from builder import ReynirCorpus


class SimilarityServer:

    """ A class that manages an in-memory dictionary of articles
        and their topic vectors, and allows similarity queries of that
        dictionary. The dictionary is refreshed upon request from the
        articles database table.
    """

    def __init__(self):
        # Do an initial load of all article topic vectors
        self._lock = Lock()
        self._timestamp = None
        self._atopics = {}
        self._corpus = None


    def _load_topics(self):
        """ Load all article topics into the self._atopics dictionary """
        self._atopics = {}
        with SessionContext(commit = True, read_only = True) as session:
            print("Starting load of all article topic vectors")
            t0 = time.time()
            # Do the next refresh from this time point
            self._timestamp = datetime.utcnow()
            q = session.query(Article).join(Root).filter(Root.visible) \
                .with_entities(Article.id, Article.topic_vector)

            for a in q.yield_per(2000):
                if a.topic_vector:
                    # Load topic vector in to a numpy array
                    self._atopics[a.id] = np.array(json.loads(a.topic_vector))

            t1 = time.time()
            print("Loading of {0} topic vectors completed in {1:.2f} seconds".format(len(self._atopics), t1 - t0))


    def article_topic(self, article_id):
        """ Return the topic vector of the article having the given uuid,
            or None if no such article exists """
        return self._atopics.get(article_id)


    def reload_topics(self):
        """ Reload all article topic vectors from the database """
        with self._lock:
            # Can't serve queries while we're doing this
            self._load_topics()


    def refresh_topics(self):
        """ Load any new article topics into the _atopics dict """
        with self._lock:
            with SessionContext(commit = True, read_only = True) as session:
                # Do the next refresh from this time point
                ts = datetime.utcnow()
                q = session.query(Article).join(Root).filter(Root.visible) \
                    .filter(Article.indexed >= self._timestamp) \
                    .with_entities(Article.id, Article.topic_vector)
                self._timestamp = ts
                count = 0
                for a in q.yield_per(100):
                    if a.topic_vector:
                        # Load topic vector in to a numpy array
                        self._atopics[a.id] = np.array(json.loads(a.topic_vector))
                        count += 1
                print("Completed refresh_topics, {0} article vectors added".format(count))


    def _iter_similarities(self, vector):
        """ Generator of (id, similarity) tuples for all articles to the given vector """
        base = np.array(vector)
        norm_base = np.dot(base, base) # This is faster than linalg.norm()
        if norm_base < 1.0e-6:
            # No data to search by
            return

        def cosine_similarity(v):
            """ Compute cosine similarity of v1 to v2: (v1 dot v2)/(|v1|*|v2|) """
            norm_v = np.dot(v, v) # This is faster than linalg.norm()
            dot_product = np.dot(v, base)
            return float(dot_product / math.sqrt(norm_v * norm_base))

        for article_id, topic_vector in self._atopics.items():
            yield article_id, cosine_similarity(topic_vector)


    def find_similar(self, n, vector):
        """ Return the N articles with the highest similarity score to the given vector,
            as a list of tuples (article_uuid, similarity) """
        if vector is None or len(vector) == 0 or vector == np.zeros(len(vector)):
            return []
        with self._lock:
            return heapq.nlargest(n,
                self._iter_similarities(vector),
                key = operator.itemgetter(1))


    def run(self, host, port):
        """ Run a similarity server serving requests that come in at the given port """
        address = (host, port) # Family is deduced to be 'AF_INET'
        # Load the secret password that clients must use to authenticate themselves
        try:
            with open("resources/SimilarityServerKey.txt", "rb") as file:
                secret_password = file.read()
        except FileNotFoundError:
            print("Unable to open resources/SimilarityServerKey.txt")
            return
        print("Listening for connections on port {0}".format(port))
        with Listener(address, authkey = secret_password) as listener:
            self._corpus = ReynirCorpus()
            self._load_topics()
            while True:
                try:
                    conn = listener.accept()
                    print('Connection accepted from {0}'.format(listener.last_accepted))
                    # Launch a thread to handle commands from this client
                    Thread(target = self._command_loop, args = (conn,)).start()
                except AuthenticationError:
                    print("Authentication failed for client")


    def _command_loop(self, conn):
        """ Run a command loop for this server inside a client thread """

        class ClientError(RuntimeError):
            """ Local exception class for handling erroneous requests from clients """
            def __init__(self, request):
                super().__init__("Invalid request received: {0!r}".format(request))

        with conn:
            # conn is automatically closed when leaving the 'with' scope
            while True:
                try:
                    request = conn.recv()

                    # Requests are sent as Python dict objects
                    if not isinstance(request, dict):
                        raise ClientError(request)

                    # The main command should be a string under the 'cmd' key
                    try:
                        cmd = request["cmd"].strip().lower()
                    except:
                        raise ClientError(request)

                    if cmd == "logout":
                        print("Client logged out")
                        break

                    if cmd == "similar":
                        # Run a similarity query
                        # Obtain number of desired results
                        try:
                            n = int(request.get("n", 10))
                        except:
                            n = 10
                        topic = None
                        if "id" in request:
                            try:
                                # Compare similarity to an article identified by UUID
                                uuid = request["id"].strip().lower()
                                topic = self.article_topic(uuid)
                            except:
                                raise ClientError(request)
                        elif "terms" in request:
                            # Compare similarity to the given terms, which are assumed to
                            # be normalized, i.e. of the form (stem, category).
                            # Examples: ('sjómaður', 'kk'), ('Jóna Hrönn Bolladóttir', 'person_kvk')
                            terms = request["terms"]
                            if not isinstance(terms, list):
                                raise ClientError(request)
                            # Convert the list of search terms to a topic vector
                            topic = self._corpus.get_topic_vector(terms)
                            print("Topic is {0}".format(topic))
                        elif "topic" in request:
                            # Compare similarity to the given topic vector
                            topic = request["topic"]
                            if not isinstance(topic, list):
                                raise ClientError(request)
                        else:
                            raise ClientError(request)
                        # Launch the command and send the reply back to the client
                        t0 = time.time()
                        result = self.find_similar(n, topic)
                        t1 = time.time()
                        conn.send(result)
                        print("find_similar() took {0:.2f} seconds".format(t1 - t0))
                    elif cmd == "refresh":
                        # Load any new article topic vectors from the articles table
                        self.refresh_topics()
                    elif cmd == "reload":
                        # Reload all article topic vectors from the articles table
                        self.reload_topics()
                    else:
                        print("Unknown command: {0}".format(cmd))

                except EOFError:
                    print("Client closed connection")
                    break

                except ClientError as e:
                    # Print a message and continue listening to commands
                    print(str(e))


if __name__ == "__main__":

    try:
        # Read configuration file
        Settings.read("Vectors.conf")
    except ConfigError as e:
        print("Configuration error: {0}".format(e))
        quit()

    # Run a similarity server on the default port
    # Modify host to 0.0.0.0 to enable outside access
    try:
        SimilarityServer().run(host = 'localhost', port = Settings.SIMSERVER_PORT)
    except OSError as e:
        import errno
        if e.errno == errno.EADDRINUSE: # Address already in use
            print("Simserver is already running on port {0}".format(Settings.SIMSERVER_PORT))
        else:
            raise


