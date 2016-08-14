#!/usr/bin/env python
"""
    Reynir: Natural language processing for Icelandic

    Web server main module

    Copyright (c) 2015 Vilhjalmur Thorsteinsson
    All rights reserved
    See the accompanying README.md file for further licensing and copyright information.

    This module is written in Python 3.2 for compatibility with PyPy3

"""

import sys
import time
import random
import re
from contextlib import closing
from datetime import datetime
from functools import wraps
from collections import OrderedDict, defaultdict
from decimal import Decimal

from flask import Flask
from flask import render_template, make_response, jsonify, redirect, url_for
from flask import request, send_from_directory
from flask.wrappers import Response

from fastparser import Fast_Parser, ParseError, ParseForestPrinter, ParseForestDumper
from ptest import run_test, Test_DB
from reducer import Reducer
from fetcher import Fetcher
from article import Article as ArticleProxy
from tree import TreeGist
from scraperdb import SessionContext, desc, Person, Article, GenderQuery, StatsQuery
from settings import Settings, ConfigError, changedlocale
from tokenizer import tokenize, TOK, correct_spaces
from query import Query, query_person, query_entity
from getimage import get_image_url
from bindb import BIN_Db

# Initialize Flask framework

app = Flask(__name__)

from flask import current_app

def debug():
    # Call this to trigger the Flask debugger on purpose
    assert current_app.debug == False, "Don't panic! You're here by request of debug()"


# Utilities for Flask/Jinja2 formatting of numbers using the Icelandic locale

def make_pattern(rep_dict):
    return re.compile("|".join([re.escape(k) for k in rep_dict.keys()]), re.M)

def multiple_replace(string, rep_dict, pattern = None):
    """ Perform multiple simultaneous replacements within string """
    if pattern is None:
        pattern = make_pattern(rep_dict)
    return pattern.sub(lambda x: rep_dict[x.group(0)], string)

_REP_DICT_IS = { ',' : '.', '.' : ',' }
_PATTERN_IS = make_pattern(_REP_DICT_IS)

@app.template_filter('format_is')
def format_is(r, decimals = 0):
    """ Flask/Jinja2 template filter to format a number for the Icelandic locale """
    fmt = "{0:,." + str(decimals) + "f}"
    return multiple_replace(fmt.format(float(r)), _REP_DICT_IS, _PATTERN_IS)

@app.template_filter('format_ts')
def format_ts(ts):
    """ Flask/Jinja2 template filter to format a timestamp """
    return str(ts)[0:19]


# Miscellaneous utility stuff

def max_age(seconds):
    """ Caching decorator for Flask - augments response with a max-age cache header """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            resp = f(*args, **kwargs)
            if not isinstance(resp, Response):
                resp = make_response(resp)
            resp.cache_control.max_age = seconds
            return resp
        return decorated_function
    return decorator

def get_json_bool(rq, name, default = False):
    """ Get a boolean from JSON encoded in a request form """
    b = rq.form.get(name)
    if b is None:
        # Not present in the form: return the default
        return default
    return isinstance(b, str) and b == "true"


# Run with profiling?
_PROFILE = False

# Default text shown in the URL/text box
_DEFAULT_TEXTS = [
    'Litla gula hænan fann fræ. Það var hveitifræ.',
    'Hver gegnir starfi seðlabankastjóra?',
    'Hvað er HeForShe?',
    'Hver er Birgitta Jónsdóttir?',
    'Hver er borgarstjóri?',
    'Hver er formaður Öryrkjabandalagsins?',
    'Hvað er Wintris?',
    'Hver er Vigdís Finnbogadóttir?',
    'Hver er Kristján Eldjárn?',
    'Hvað er Dominos?',
    'Segðu mér frá Sigríði Ingibjörgu Ingadóttur',
    'Hver er forstjóri Landsvirkjunar?',
    'Hver gegnir starfi forstjóra Orkuveitu Reykjavíkur?',
    'Hver er þjóðleikhússtjóri?',
    'Hver er fyrirliði íslenska landsliðsins?',
    'Hver er forsetaframbjóðandi?',
    'Hver er Trayvon Martin?',
    'Hver er forstjóri Google?'
]

# Default number of top news items to show in front page list
_TOP_NEWS_LENGTH = 20

# Default number of top persons to show in front page list
_TOP_PERSONS_LENGTH = 20

# Maximum length of incoming GET/POST parameters
_MAX_URL_LENGTH = 512
_MAX_UUID_LENGTH = 36


def profile(func, *args, **kwargs):
    """ Profile the processing of text or URL """

    import cProfile as profile

    filename = 'Reynir.profile'

    pr = profile.Profile()
    result = pr.runcall(func, *args, **kwargs)
    pr.dump_stats(filename)

    return result


def parse(toklist, single, use_reducer, dump_forest = False, keep_trees = False, root = None):
    """ Parse the given token list and return a result dict """

    # Count sentences
    num_sent = 0
    num_parsed_sent = 0
    total_ambig = 0.0
    total_tokens = 0
    sent = []
    sent_begin = 0

    # Accumulate parsed sentences in a text dump format
    trees = OrderedDict()
    # Accumulate sentences that fail to parse
    failures = []

    with Fast_Parser(verbose = False, root = root) as bp: # Don't emit diagnostic messages

        version = bp.version
        rdc = Reducer(bp.grammar)

        for ix, t in enumerate(toklist):
            t0 = t[0]
            if t0 == TOK.S_BEGIN:
                sent = []
                sent_begin = ix
            elif t0 == TOK.S_END:
                slen = len(sent)
                if not slen:
                    continue
                # Parse the accumulated sentence
                num_sent += 1
                err_index = None
                num = 0 # Number of tree combinations in forest
                score = 0 # Reducer score of the best parse tree

                try:
                    # Parse the sentence
                    forest = bp.go(sent)
                    if forest:
                        num = Fast_Parser.num_combinations(forest)

                        if single and dump_forest:
                            # Dump the parse tree to parse.txt
                            with open("parse.txt", mode = "w", encoding= "utf-8") as f:
                                print("Reynir parse tree for sentence '{0}'".format(" ".join(sent)), file = f)
                                print("{0} combinations\n".format(num), file = f)
                                if num < 10000:
                                    ParseForestPrinter.print_forest(forest, file = f)
                                else:
                                    print("Too many combinations to dump", file = f)

                    if use_reducer and num > 1:
                        # Reduce the resulting forest
                        forest, score = rdc.go_with_score(forest)
                        # assert Fast_Parser.num_combinations(forest) == 1

                        if single and Settings.DEBUG:
                            print(ParseForestDumper.dump_forest(forest))

                        num = 1

                except ParseError as e:
                    forest = None
                    # Obtain the index of the offending token
                    err_index = e.token_index

                if Settings.DEBUG:
                    print("Parsed sentence of length {0} with {1} combinations, score {2}{3}"
                        .format(slen, num, score,
                            "\n" + (" ".join(s[1] for s in sent) if num >= 100 else "")))
                if num > 0:
                    num_parsed_sent += 1
                    # Calculate the 'ambiguity factor'
                    ambig_factor = num ** (1 / slen)
                    # Do a weighted average on sentence length
                    total_ambig += ambig_factor * slen
                    total_tokens += slen
                    if keep_trees:
                        # We want to keep the trees for further processing down the line:
                        # reduce and dump the best tree to text
                        if num > 1:
                            # Reduce the resulting forest before dumping it to text format
                            forest = rdc.go(forest)
                        trees[num_sent] = ParseForestDumper.dump_forest(forest)
                else:
                    # Error: store the error token index in the parse tree
                    if keep_trees:
                        trees[num_sent] = "E{0}".format(slen - 1 if err_index is None else err_index)
                    failures.append(" ".join(t.txt for t in sent))

                # Mark the sentence beginning with the number of parses
                # and the index of the offending token, if an error occurred
                toklist[sent_begin] = TOK.Begin_Sentence(num_parses = num, err_index = err_index)
            elif t0 == TOK.P_BEGIN:
                pass
            elif t0 == TOK.P_END:
                pass
            else:
                sent.append(t)

    result = dict(
        version = version,
        tokens = toklist,
        tok_num = len(toklist),
        num_sent = num_sent,
        num_parsed_sent = num_parsed_sent,
        avg_ambig_factor = (total_ambig / total_tokens) if total_tokens > 0 else 1.0
    )

    # noinspection PyRedundantParentheses
    return (result, trees, failures)


def prepare(toklist, article):
    """ Prepare the given token list for display and return a result dict """

    # Count sentences
    num_sent = 0
    num_parsed_sent = 0
    total_tokens = 0
    sent = []
    sent_begin = 0

    tree = TreeGist() # We only need a gist of the tree
    tree.load(article.tree)

    for ix, t in enumerate(toklist):
        if t[0] == TOK.S_BEGIN:
            sent = []
            sent_begin = ix
        elif t[0] == TOK.S_END:
            slen = len(sent)
            if not slen:
                continue
            num_sent += 1
            # Parse the accumulated sentence
            err_index = None # Index of offending token within sentence, if any
            num = 1 if num_sent in tree else 0
            if num > 0:
                num_parsed_sent += 1
                total_tokens += slen
            else:
                # If no parse, ask the tree for the error token index
                err_index = tree.err_index(num_sent)
            # Mark the sentence beginning with the number of parses
            # and the index of the offending token, if an error occurred
            toklist[sent_begin] = TOK.Begin_Sentence(num_parses = num, err_index = err_index)
        elif t[0] == TOK.P_BEGIN:
            pass
        elif t[0] == TOK.P_END:
            pass
        else:
            sent.append(t)

    result = dict(
        version = "N/A",
        tokens = toklist,
        tok_num = len(toklist),
        num_sent = num_sent,
        num_parsed_sent = num_parsed_sent,
        avg_ambig_factor = article.ambiguity
    )

    return result


def add_entity_to_register(name, register, session):
    """ Add the entity name and the 'best' definition to the given name register dictionary """
    if name in register:
        # Already have a title for this name
        return
    # Use the query module to return definitions for an entity
    rl = query_entity(session, name)
    if rl:
        register[name] = correct_spaces(rl[0][0])

    # Older code to extract definitions directly from database query
    # titles = defaultdict(int)
    # for p in rl:
    #     # Collect and count the titles
    #     titles[p[0]] += 1
    # if sum(cnt >= 4 for cnt in titles.values()) >= 2:
    #     # More than one title with four or more instances:
    #     # reduce the choices to just those and decide based on length
    #     titles = { key: 0 for key, val in titles.items() if val >= 4 }
    # if titles:
    #     # Pick the most popular title, or the longer one if two are equally popular
    #     title = sorted([(cnt, len(t), t) for t, cnt in titles.items()])[-1][2]
    #     # Add it to the register, after correcting spacing
    #     register[name] = correct_spaces(title)


def add_name_to_register(name, register, session):
    """ Add the name and the 'best' title to the given name register dictionary """
    if name in register:
        # Already have a title for this name
        return
    # Use the query module to return titles for a person
    rl = query_person(session, name)
    if rl:
        register[name] = correct_spaces(rl[0][0])

    # Older code to extract titles directly from database query results        
    # titles = defaultdict(int)
    # for p in rl:
    #     # Collect and count the titles
    #     titles[p[0]] += 1
    # if sum(cnt >= 4 for cnt in titles.values()) >= 2:
    #     # More than one title with four or more instances:
    #     # reduce the choices to just those and decide based on length
    #     titles = { key: 0 for key, val in titles.items() if val >= 4 }
    # if titles:
    #     # Pick the most popular title, or the longer one if two are equally popular
    #     title = sorted([(cnt, len(t), t) for t, cnt in titles.items()])[-1][2]
    #     # Add it to the register, after correcting spacing
    #     register[name] = correct_spaces(title)


def create_name_register(tokens, session):
    """ Assemble a register of names and titles from the token list """
    register = { }
    for t in tokens:
        if t.kind == TOK.PERSON:
            gn = t.val
            for pn in gn:
                add_name_to_register(pn.name, register, session)
    with changedlocale() as strxfrm:
        reglist = sorted(
            [ dict(name = key, title = val) for key, val in register.items() ],
            key = lambda x: strxfrm(x["name"])
        )
    if Settings.DEBUG:
        print("Register is: {0}".format(reglist))
    return reglist


def top_news(limit = _TOP_NEWS_LENGTH):
    """ Return a list of top recent news """
    toplist = []
    topdict = dict()
    now = datetime.utcnow()
    MARGIN = 10 # Get more articles than requested in case there are duplicates

    with SessionContext(commit = True) as session:

        q = session.query(Article) \
            .filter(Article.tree != None) \
            .filter(Article.timestamp != None) \
            .filter(Article.timestamp < now) \
            .filter(Article.heading > "") \
            .order_by(desc(Article.timestamp))[0:limit + MARGIN]

        class ArticleDisplay:

            """ Utility class to carry information about an article to the web template """

            def __init__(self, heading, original_ts, timestamp, url, uuid, num_sentences, num_parsed, icon):
                self.heading = heading
                self.original_ts = original_ts
                self.timestamp = timestamp
                self.url = url
                self.uuid = uuid
                self.num_sentences = num_sentences
                self.num_parsed = num_parsed
                self.icon = icon

            @property
            def width(self):
                """ The ratio of parsed sentences to the total number of sentences,
                    expressed as a percentage string """
                if self.num_sentences == 0:
                    return "0%"
                return "{0}%".format((100 * self.num_parsed) // self.num_sentences)

        for a in q:
            # Collect and count the titles
            icon = a.root.domain + ".ico"

            d = ArticleDisplay(heading = a.heading, original_ts = a.timestamp, timestamp = str(a.timestamp)[11:16],
                url = a.url, uuid = a.id, num_sentences = a.num_sentences, num_parsed = a.num_parsed, icon = icon)

            # Have we seen the same heading on the same domain?
            t = (a.root.domain, a.heading)
            if t in topdict:
                # Same domain+heading already in the list
                i = topdict[t]
                if d.original_ts > toplist[i].original_ts:
                    # The new entry is newer: replace the old one
                    toplist[i] = d
                # Otherwise, ignore the new entry and continue
            else:
                # New heading: note its index in the list
                llist = len(toplist)
                topdict[t] = llist
                toplist.append(d)
                if llist + 1 >= limit:
                    break

    return toplist


def top_persons(limit = _TOP_PERSONS_LENGTH):
    """ Return a list of names and titles appearing recently in the news """
    toplist = dict()
    bindb = BIN_Db.get_db()

    with SessionContext(commit = True) as session:

        q = session.query(Person.name, Person.title, Person.article_url, Article.id) \
            .join(Article) \
            .order_by(desc(Article.timestamp))[0:limit * 2] # Go through up to 2 * N records

        for p in q:
            # Insert the name into the list if it's not already there,
            # or if the new title is longer than the previous one
            if p.name not in toplist or len(p.title) > len(toplist[p.name][0]):
                toplist[p.name] = (correct_spaces(p.title), p.article_url, p.id, bindb.lookup_name_gender(p.name))
                if len(toplist) >= limit:
                    # We now have as many names as we initially wanted: terminate the loop
                    break

    with changedlocale() as strxfrm:
        # Convert the dictionary to a sorted list of dicts
        return sorted(
            [ dict(name = name, title = tu[0], gender = tu[3], url = tu[1], uuid = tu[2]) for name, tu in toplist.items() ],
            key = lambda x: strxfrm(x["name"])
        )


def process_query(session, toklist, result):
    """ Check whether the parse tree is describes a query, and if so, execute the query,
        store the query answer in the result dictionary and return True """
    q = Query(session)
    if not q.parse(toklist, result):
        # Not able to parse this as a query
        return False
    if not q.execute():
        # This is a query, but its execution failed for some reason: return the error
        result["error"] = q.error()
        return True
    # Successful query: return the answer in response
    result["response"] = q.answer()
    # ...and the query type, as a string ('Person', 'Entity', 'Title' etc.)
    result["qtype"] = qt = q.qtype()
    if qt == "Person":
        # For a person query, add an image (if available)
        img = get_image_url(q.key())
        if img is not None:
            result["image"] = dict(src = img.src,
                width = img.width, height = img.height,
                link = img.link, origin = img.origin)
    return True


# Note: Endpoints ending with .api are configured not to be cached by nginx
@app.route("/analyze.api", methods=['POST'])
def analyze():
    """ Analyze text from a given URL """

    url = request.form.get("url", "").strip()
    use_reducer = not get_json_bool(request, "noreduce")
    auto_uppercase = get_json_bool(request, "autouppercase", True)
    dump_forest = "dump" in request.form
    metadata = None
    # Single sentence (True) or contiguous text from URL (False)?
    single = False
    keep_trees = False
    is_query = False

    t0 = time.time()

    with SessionContext(commit = True) as session:

        if url.startswith("http:") or url.startswith("https:"):
            # Scrape the URL, tokenize the text content and return the token list
            metadata, generator = Fetcher.tokenize_url(url)
            toklist = list(generator)
            # If this is an already scraped URL, keep the parse trees and update
            # the database with the new parse
            keep_trees = Fetcher.is_known_url(url, session)
        else:
            single = True
            # Tokenize the text entered as-is and return the token list.
            # In this case, there's no metadata.
            # We specify auto_uppercase to convert lower case words to upper case
            # if the text is all lower case. The text may for instance
            # be coming from a speech recognizer.

            # Demarcate paragraphs in the input
            txt = Fetcher.mark_paragraphs(url)
            toklist = list(tokenize(txt, auto_uppercase = txt.islower() if auto_uppercase else False))
            result = dict()
            is_query = process_query(session, toklist, result)
            if not is_query:
                use_reducer = True

        if is_query:

            if Settings.DEBUG:
                # The query string as seen by the parser
                actual_q = correct_spaces(" ".join(t.txt or "" for t in toklist))
                print("Query is: '{0}'".format(actual_q))

            result["q"] = url

        else:

            # Not a query: parse normally

            tok_time = time.time() - t0

            t0 = time.time()

            if _PROFILE:
                result, trees, failures = profile(parse, toklist, single, use_reducer, dump_forest, keep_trees)
            else:
                result, trees, failures = parse(toklist, single, use_reducer, dump_forest, keep_trees)

            parse_time = time.time() - t0

            # Add a name register to the result
            result["register"] = create_name_register(result["tokens"], session)
            # Add information from parser
            result["metadata"] = metadata
            result["tok_time"] = tok_time
            result["parse_time"] = parse_time

        result["is_query"] = is_query # True if we are returning a query result, not tokenized (and parsed) text

        #if keep_trees and not single and metadata is not None:
        #    # Save a new parse result for an article
        #    if Settings.DEBUG:
        #        print("Storing a new parse tree for url {0}".format(url))
        #    Scraper.store_parse(url, result, trees, failures, enclosing_session = session)

    # with open("tokens.log", mode = "w", encoding="utf-8") as f:
    #     indent = 0
    #     for t in result["tokens"]:
    #         if t.kind in { TOK.P_END, TOK.S_END }:
    #             indent -= 1
    #         print("{2}{0} {1}".format(TOK.descr[t.kind], "" if t.txt is None else t.txt, "  " * indent), file = f)
    #         if t.kind in { TOK.P_BEGIN, TOK.S_BEGIN }:
    #             indent += 1

    # Return the tokens as a JSON structure to the client
    return jsonify(result = result)


# Note: Endpoints ending with .api are configured not to be cached by nginx
@app.route("/display.api", methods=['POST'])
def display():
    """ Display an already parsed article with a given URL """

    url = request.form.get("url", "").strip()

    if not url.startswith("http:") and not url.startswith("https:"):
        # Not a valid URL
        return jsonify(result = None, error = "Invalid URL")

    with SessionContext(commit = True) as session:

        t0 = time.time()

        # Find the HTML in the scraper database, tokenize the text content and return the token list
        article, metadata, content = Fetcher.fetch_article(url, session)

        metadata, generator = Fetcher.tokenize_url(url, None if article is None else (metadata, content))

        toklist = list(generator)

        tok_time = time.time() - t0

        t0 = time.time()

        if article is None or not article.tree:
            # We must do a full parse of the toklist
            result, _, _ = parse(toklist, single = False, use_reducer = False)
        else:
            # Re-use a previous parse
            result = prepare(toklist, article)

        parse_time = time.time() - t0

        # Add a name register to the result
        result["register"] = create_name_register(result["tokens"], session)

        result["tok_time"] = tok_time
        result["parse_time"] = parse_time

        result["metadata"] = metadata
        # Return the tokens as a JSON structure to the client
        return jsonify(result = result)


# Note: Endpoints ending with .api are configured not to be cached by nginx
@app.route("/reparse.api", methods=['POST'])
def reparse():
    """ Reparse an article with a given UUID """

    uuid = request.form.get("id", "").strip()[0:_MAX_UUID_LENGTH]
    tokens = None
    register = { }
    stats = { }

    with SessionContext(commit = True) as session:
        # Load the article
        a = ArticleProxy.load_from_uuid(uuid, session)
        if a is not None:
            # Found: parse it and store the updated version
            a.parse(session)
            # Save the tokens
            tokens = a.tokens
            # Build register of person names
            for name in a.person_names():
                add_name_to_register(name, register, session)
            # Add register of entity names
            for name in a.entity_names():
                add_entity_to_register(name, register, session)
            stats = dict(
                num_tokens = a.num_tokens,
                num_sentences = a.num_sentences,
                num_parsed = a.num_parsed,
                ambiguity = a.ambiguity)

    # Return the tokens as a JSON structure to the client,
    # along with a name register and article statistics
    return jsonify(result = tokens, register = register, stats = stats)


# Note: Endpoints ending with .api are configured not to be cached by nginx
@app.route("/query.api", methods=['POST'])
def query():
    """ Respond to a query string """

    q = request.form.get("q", "").strip()
    # Auto-uppercasing can be turned off by sending autouppercase: false in the query JSON
    auto_uppercase = get_json_bool(request, "autouppercase", True)

    toklist = list(tokenize(q, auto_uppercase = q.islower() if auto_uppercase else False))
    result = dict()

    with SessionContext(commit = True) as session:

        if Settings.DEBUG:
            # Log the query string as seen by the parser
            actual_q = correct_spaces(" ".join(t.txt or "" for t in toklist))
            print("Query is: '{0}'".format(actual_q))

        # Try to parse and process as a query
        is_query = process_query(session, toklist, result)


    result["is_query"] = is_query
    result["q"] = q

    return jsonify(result = result)


def make_grid(w):
    """ Make a 2d grid from a flattened parse schema """

    def make_schema(w):
        """ Create a flattened parse schema from the forest w """

        def _part(w, level, suffix):
            """ Return a tuple (colheading + options, start_token, end_token, partlist, info)
                where the partlist is again a list of the component schemas - or a terminal
                matching a single token - or None if empty """
            if w is None:
                # Epsilon node: return empty list
                return None
            if w.is_token:
                return ([ level ] + suffix, w.start, w.end, None, (w.terminal, w.token.text))
            # Interior nodes are not returned
            # and do not increment the indentation level
            if not w.is_interior:
                level += 1
            # Accumulate the resulting parts
            plist = [ ]
            ambig = w.is_ambiguous
            add_suffix = [ ]

            for ix, pc in enumerate(w.enum_children()):
                prod, f = pc
                if ambig:
                    # Uniquely identify the available parse options with a coordinate
                    add_suffix = [ ix ]

                def add_part(p):
                    """ Add a subtuple p to the part list plist """
                    if p:
                        if p[0] is None:
                            # p describes an interior node
                            plist.extend(p[3])
                        elif p[2] > p[1]:
                            # Only include subtrees that actually contain terminals
                            plist.append(p)

                if isinstance(f, tuple):
                    add_part(_part(f[0], level, suffix + add_suffix))
                    add_part(_part(f[1], level, suffix + add_suffix))
                else:
                    add_part(_part(f, level, suffix + add_suffix))

            if w.is_interior:
                # Interior node: relay plist up the tree
                return (None, 0, 0, plist, None)
            # Completed nonterminal
            assert w.is_completed
            assert w.nonterminal is not None
            return ([level - 1] + suffix, w.start, w.end, plist, w.nonterminal)

        # Start of make_schema

        if w is None:
            return None
        return _part(w, 0, [ ])

    # Start of make_grid

    if w is None:
        return None
    schema = make_schema(w)
    assert schema[1] == 0
    cols = [] # The columns to be populated
    NULL_TUPLE = tuple()

    def _traverse(p):
        """ Traverse a schema subtree and insert the nodes into their
            respective grid columns """
        # p[0] is the coordinate of this subtree (level + suffix)
        # p[1] is the start column of this subtree
        # p[2] is the end column of this subtree
        # p[3] is the subpart list
        # p[4] is the nonterminal or terminal/token at the head of this subtree
        col, option = p[0][0], p[0][1:] # Level of this subtree and option

        if not option:
            # No option: use a 'clean key' of NULL_TUPLE
            option = NULL_TUPLE
        else:
            # Convert list to a frozen (hashable) tuple
            option = tuple(option)

        while len(cols) <= col:
            # Add empty columns as required to reach this level
            cols.append(dict())

        # Add a tuple describing the rows spanned and the node info
        if option not in cols[col]:
            # Put in a dictionary entry for this option
            cols[col][option] = []
        cols[col][option].append((p[1], p[2], p[4]))

        # Navigate into subparts, if any
        if p[3]:
            for subpart in p[3]:
                _traverse(subpart)

    _traverse(schema)
    # Return a tuple with the grid and the number of tokens
    return (cols, schema[2])


@app.route("/parsegrid", methods=['POST'])
def parse_grid():
    """ Show the parse grid for a particular parse tree of a sentence """

    MAX_LEVEL = 32 # Maximum level of option depth we can handle
    txt = request.form.get('txt', "")
    parse_path = request.form.get('option', "")
    debug_mode = get_json_bool(request, 'debug')
    use_reducer = not ("noreduce" in request.form)

    # Tokenize the text
    tokens = list(tokenize(txt))

    # Parse the text
    with Fast_Parser(verbose = False) as bp: # Don't emit diagnostic messages
        err = dict()
        grammar = bp.grammar
        try:
            forest = bp.go(tokens)
        except ParseError as e:
            err["msg"] = str(e)
            # Relay information about the parser state at the time of the error
            err["info"] = None # e.info
            forest = None

    # Find the number of parse combinations
    combinations = 0 if forest is None else Fast_Parser.num_combinations(forest)
    score = 0

    if Settings.DEBUG:
        # Dump the parse tree to parse.txt
        with open("parse.txt", mode = "w", encoding= "utf-8") as f:
            if forest is not None:
                print("Reynir parse forest for sentence '{0}'".format(txt), file = f)
                print("{0} combinations\n".format(combinations), file = f)
                if combinations < 10000:
                    ParseForestPrinter.print_forest(forest, file = f)
                else:
                    print("Too many combinations to dump", file = f)
            else:
                print("No parse available for sentence '{0}'".format(txt), file = f)

    if forest is not None and use_reducer:
        # Reduce the parse forest
        forest, score = Reducer(grammar).go_with_score(forest)
        #if Settings.DEBUG:
            # Dump the reduced tree along with node scores
            #with open("reduce.txt", mode = "w", encoding= "utf-8") as f:
            #    print("Reynir parse tree for sentence '{0}' after reduction".format(txt), file = f)
            #    ParseForestPrinter.print_forest(forest, file = f, show_scores = True)

    # Make the parse grid with all options
    grid, ncols = make_grid(forest) if forest else ([], 0)
    # The grid is columnar; convert it to row-major
    # form for convenient translation into HTML
    # There will be as many columns as there are tokens
    nrows = len(grid)
    tbl = [ [] for _ in range(nrows) ]
    # Info about previous row spans
    rs = [ [] for _ in range(nrows) ]

    # The particular option path we are displaying
    if not parse_path:
        # Not specified: display the all-zero path
        path = [(0,) * i for i in range(1, MAX_LEVEL)]
    else:
        # Disassemble the passed-in path

        def toint(s):
            """ Safe conversion of string to int """
            try:
                n = int(s)
            except ValueError:
                n = 0
            return n if n >= 0 else 0

        p = [ toint(s) for s in parse_path.split("_") ]
        path = [tuple(p[0 : i + 1]) for i in range(len(p))]

    # This set will contain all option path choices
    choices = set()
    NULL_TUPLE = tuple()

    for gix, gcol in enumerate(grid):
        # gcol is a dictionary of options
        # Accumulate the options that we want do display
        # according to chosen path
        cols = gcol[NULL_TUPLE] if NULL_TUPLE in gcol else [] # Default content
        # Add the options we're displaying
        for p in path:
            if p in gcol:
                cols.extend(gcol[p])
        # Accumulate all possible path choices
        choices |= gcol.keys()
        # Sort the columns that will be displayed
        cols.sort(key = lambda x: x[0])
        col = 0
        for startcol, endcol, info in cols:
            #assert isinstance(info, Nonterminal) or isinstance(info, tuple)
            if col < startcol:
                gap = startcol - col
                gap -= sum(1 for c in rs[gix] if c < startcol)
                if gap > 0:
                    tbl[gix].append((gap, 1, "", ""))
            rowspan = 1
            if isinstance(info, tuple):
                cls = { "terminal" }
                rowspan = nrows - gix
                for i in range(gix + 1, nrows):
                    # Note the rowspan's effect on subsequent rows
                    rs[i].append(startcol)
            else:
                cls = { "nonterminal" }
                # Get the 'pure' name of the nonterminal in question
                #assert isinstance(info, Nonterminal)
                info = info.name
            if endcol - startcol == 1:
                cls |= { "vertical" }
            tbl[gix].append((endcol-startcol, rowspan, info, cls))
            col = endcol
        ncols_adj = ncols - len(rs[gix])
        if col < ncols_adj:
            tbl[gix].append((ncols_adj - col, 1, "", ""))
    # Calculate the unique path choices available for this parse grid
    choices -= { NULL_TUPLE } # Default choice: don't need it in the set
    unique_choices = choices.copy()
    for c in choices:
        # Remove all shorter prefixes of c from the unique_choices set
        unique_choices -= { c[0:i] for i in range(1, len(c)) }
    # Create a nice string representation of the unique path choices
    uc_list = [ "_".join(str(c) for c in choice) for choice in unique_choices ]
    if not parse_path:
        # We are displaying the longest possible all-zero choice: find it
        i = 0
        while (0,) * (i + 1) in unique_choices:
            i += 1
        parse_path = "_".join(["0"] * i)

    return render_template("parsegrid.html", txt = txt, err = err, tbl = tbl,
        combinations = combinations, score = score, debug_mode = debug_mode,
        choice_list = uc_list, parse_path = parse_path)


# Note: Endpoints ending with .api are configured not to be cached by nginx
# @app.route("/addsentence.api", methods=['POST'])
def add_sentence():
    """ Add a sentence to the test database """
    sentence = request.form.get('sentence', "")
    # The sentence may be one that should parse and give us ideally one result tree,
    # or one that is wrong and should not parse, giving 0 result trees.
    should_parse = get_json_bool(request, 'shouldparse', True)
    result = False
    if sentence:
        try:
            with closing(Test_DB.open_db()) as db:
                result = db.add_sentence(sentence, target = 1 if should_parse else 0)
        except Exception as e:
            return jsonify(result = False, err = str(e))
    return jsonify(result = result)


@app.route("/genders", methods=['GET'])
@max_age(seconds = 5 * 60)
def genders():
    """ Render a page with gender statistics """

    with SessionContext(commit = True) as session:

        gq = GenderQuery()
        result = gq.execute(session)

        total = dict(kvk = Decimal(), kk = Decimal(), hk = Decimal(), total = Decimal())
        for r in result:
            total["kvk"] += r.kvk
            total["kk"] += r.kk
            total["hk"] += r.hk
            total["total"] += r.kvk + r.kk + r.hk

        return render_template("genders.html", result = result, total = total)


@app.route("/stats", methods=['GET'])
@max_age(seconds = 5 * 60)
def stats():
    """ Render a page with article statistics """

    with SessionContext(commit = True) as session:

        sq = StatsQuery()
        result = sq.execute(session)

        total = dict(art = Decimal(), sent = Decimal(), parsed = Decimal())
        for r in result:
            total["art"] += r.art
            total["sent"] += r.sent
            total["parsed"] += r.parsed

        return render_template("stats.html", result = result, total = total)


@app.route("/about")
@max_age(seconds = 10 * 60)
def about():
    """ Handler for an 'About' page """
    return render_template("about.html")


@app.route("/news")
@max_age(seconds = 60)
def news():
    """ Handler for a page with a top news list """
    return render_template("news.html", articles = top_news())


@app.route("/people")
@max_age(seconds = 60)
def people():
    """ Handler for a page with a list of people recently appearing in news """
    return render_template("people.html", persons = top_persons())


@app.route("/analysis")
def analysis():
    """ Handler for a page with grammatical analysis of user-entered text """
    txt = request.args.get("txt", "")[0:2048] # Don't allow more than 2K of text to be passed via the URL
    return render_template("analysis.html", default_text = txt)


@app.route("/article")
def article():
    """ Handler for a page displaying a single article """
    uuid = request.args.get("id", None)
    if uuid:
        uuid = uuid.strip()[0:_MAX_UUID_LENGTH]
    article = None
    # Load the article
    with SessionContext(commit = True) as session:
        if uuid:
            article = session.query(Article).filter(Article.id == uuid).one_or_none()
        if article is None:
            return redirect(url_for('main'))
        return render_template("article.html", article = article)


@app.route("/page")
def page():
    """ Handler for a page displaying the parse of an arbitrary web page by URL
        or an already scraped article by UUID """
    url = request.args.get("url", None)
    uuid = request.args.get("id", None)
    if url:
        url = url.strip()[0:_MAX_URL_LENGTH]
    if uuid:
        uuid = uuid.strip()[0:_MAX_UUID_LENGTH]
    if url:
        # URL has priority, if both are specified
        uuid = None
    if not url and not uuid:
        # !!! TODO: Separate error page
        return redirect(url_for('main'))

    register = []
    with SessionContext(commit = True) as session:

        if uuid:
            a = ArticleProxy.load_from_uuid(uuid, session)
        elif url.startswith("http:") or url.startswith("https:"):
            a = ArticleProxy.load_from_url(url, session)
        else:
            a = None

        if a is None:
            # !!! TODO: Separate error page
            return redirect(url_for('main'))

        # Prepare the article for display (may cause it to be parsed and stored)
        a.prepare(session)
        register = { }
        # Build register of person names
        for name in a.person_names():
            add_name_to_register(name, register, session)
        # Add register of entity names
        for name in a.entity_names():
            add_entity_to_register(name, register, session)

        return render_template("page.html", article = a, register = register)


@app.route("/test")
def test():
    """ Handler for a page of sentences for testing """
    # Run test and show the result
    fp = Fast_Parser(verbose = False) # Don't emit diagnostic messages
    return render_template("test.html", result = run_test(fp))


@app.route("/")
@max_age(seconds = 60)
def main():
    """ Handler for the main (index) page """
    txt = request.args.get("txt", None)
    if txt:
        txt = txt.strip()
    if not txt:
        # Select a random default text
        txt = _DEFAULT_TEXTS[random.randint(0, len(_DEFAULT_TEXTS) - 1)]
    return render_template("main-bootstrap.html", default_text = txt)


# Flask handlers

@app.route('/fonts/<path:path>')
@max_age(seconds = 24 * 60 * 60) # Cache font for 24 hours
def send_font(path):
    return send_from_directory('fonts', path)

# noinspection PyUnusedLocal
@app.errorhandler(404)
def page_not_found(e):
    """ Return a custom 404 error """
    return 'Þessi vefslóð er ekki rétt', 404

@app.errorhandler(500)
def server_error(e):
    """ Return a custom 500 error """
    return 'Eftirfarandi villa kom upp: {}'.format(e), 500


# Initialize the main module

t0 = time.time()
try:
    # Read configuration file
    Settings.read("Reynir.conf")
except ConfigError as e:
    print("Configuration error: {0}".format(e))
    quit()

if Settings.DEBUG:
    print("Settings loaded in {0:.2f} seconds".format(time.time() - t0))
    print("Running Reynir with debug={0}, host={1}, db_hostname={2}"
        .format(Settings.DEBUG, Settings.HOST, Settings.DB_HOSTNAME))


if __name__ == "__main__":

    # Run a default Flask web server for testing if invoked directly as a main program

    args = sys.argv
    if len(args) == 2 and args[1] in ("--profile", "-p"):
        _PROFILE = True
        print("Profiling enabled")

    # Additional files that should cause a reload of the web server application
    # Note: Reynir.grammar is automatically reloaded if its timestamp changes
    extra_files = [ 'Reynir.conf', 'Verbs.conf', 'Main.conf', 'Prefs.conf', 'Abbrev.conf' ]

    from socket import error as socket_error
    import errno
    try:
        # Run the Flask web server application
        app.run(debug=Settings.DEBUG, host=Settings.HOST, use_reloader=True,
            extra_files = extra_files)
    except socket_error as e:
        if e.errno == errno.EADDRINUSE: # Address already in use
            print("Reynir is already running at host {0}".format(Settings.HOST))
        else:
            raise
    finally:
        # Scraper.cleanup()
        pass

else:

    # Running as a server module: pre-load the grammar into memory
    with Fast_Parser() as fp:
        pass

