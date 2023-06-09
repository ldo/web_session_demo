#+
# Try creating a web page that keeps a WebSocket connection alive, so
# the server can detect the moment the page is closed (to within the
# chosen ping interval). This module is intended to be invoked via
# some ASGI framework, e.g. using uvicorn:
#
#   uvicorn --port 6502 web_session_demo:main
#
# then watch the info-level log messages as you open
# <http://127.0.0.1:6502/> in your browser, leave it open for a few
# seconds, and then close the page. The closing of the WebSocket
# connection should be reported immediately.
#
# This code sets a unique session ID cookie to each client, so it can
# match up the WebSocket connection with the corresponding client
# that has the web page open. In a real-world application, no doubt
# that session ID would be generated as a result of a valid client
# authentication exchange.
#
# This program also allows a user to open multiple windows/tabs
# sharing the same session cookie, and you will the WebSocket connection
# count increase/decrease accordingly.
#
# Note that the session cookie has a timeout. This has to be refreshed
# via regular HTTP requests: WebSocket requests on their own are not
# sufficient to keep cookies alive. I have left this as a deliberate
# bug in this program to illustrate the issue. The user can of course
# manually refresh the page to maintain the session cookie.
#
# ASGI specification: <https://asgi.readthedocs.io/en/latest/specs/index.html>.
#
# You can control the logging level via the LOGLEVEL environment
# variable. E.g. set LOGLEVEL=10 to enable full debugging messages.
# For more info about Python’s logging module, see
# <https://docs.python.org/3/library/logging.html>.
#
# Copyright 2023 by Lawrence D'Oliveiro <ldo@geek-central.gen.nz>. This
# script is licensed CC0
# <https://creativecommons.org/publicdomain/zero/1.0/>; do with it
# what you will.
#-

import sys
import os
import enum
import io
import time
import random
import asyncio
import logging
import html

#+
# Useful stuff
#-

class WEBSOCK_CLOSE(enum.IntEnum) :
    "some useful WebSocket codes to use on connection close."
    # codes come from RFC6455 <https://www.rfc-editor.org/rfc/rfc6455>
    NORMAL = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    DATA_ERROR = 1003
    POLICY_VIOLATION = 1008
    WTF = 1011 # Weird Technical Failure
#end WEBSOCK_CLOSE

#+
# Logging
#-

LOGGING_NAME = "web_session_demo"
  # identifies my messages in the logging module

def get_logger() :
    "retrieves the global logger instance, suitably configuring" \
    " it as necessary."
    logger = logging.getLogger(LOGGING_NAME)
    try :
        loglevel = int(os.getenv("LOGLEVEL", ""))
        if loglevel < 0 :
            raise ValueError("invalid loglevel")
        #end if
    except ValueError :
        loglevel = logging.INFO
    #end try
    if loglevel != None :
        logger.setLevel(loglevel)
    #end if
    if not logger.hasHandlers() :
        # uvicorn CLI command seems to quietly ignore my log messages,
        # so I insert my own logging setup to get around this.
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter(fmt = "%(levelname)-8s: %(message)s"))
        logger.addHandler(stderr_handler)
        logger.propagate = False
    #end if
    return \
        logger
#end get_logger

def get_cookies(scope) :
    "decodes any cookie specs in the headers part of scope."
    cookies = {}
    for keyword, value in scope["headers"] :
        if keyword == b"cookie" :
            try :
                value = value.decode()
            except UnicodeDecodeError :
                value = None
            #end try
            if value != None :
                for item in value.split("; ") :
                    try :
                        name, val = item.split("=", 1)
                    except ValueError :
                        name = None
                    #end try
                    if name != None :
                        cookies[name] = val
                    #end if
                #end for
            #end if
        #end if
    #end for
    return \
        cookies
#end get_cookies

#+
# Mainline
#-

MSG_INTERVAL = 10.0
  # interval in seconds to do a dummy WebSocket message exchange. This
  # does not affect the detection of the closing of the WebSocket
  # connection, which should always happen immediately.
SESSION_TIMEOUT = 30.0
  # Keep sessions valid for at least this long after last communication.
  # Using an unreasonably short value for testing.

sessions = {}
  # keys are valid session cookies, values are dicts with entries:
  #     expires -- cookie expiry time (refreshed on subsequent HTTP accesses)
  #     count   -- the number of current WebSocket connections
  # Special action should probably be taken when the count field goes to zero.

timeout_idle_sessions_task = None
  # currently-running instance of timeout_idle_sessions(), if any

async def timeout_idle_sessions(logger) :
    global timeout_idle_sessions_task
    logger.debug("timeout_idle_sessions startup")
    while True :
        logger.debug("timeout_idle_sessions run")
        now = time.time()
        to_delete = set()
        next_expiry = None
        for sesid, ses in sessions.items() :
            if ses["expires"] <= now :
                to_delete.add(sesid)
            else :
                if next_expiry == None :
                    next_expiry = ses["expires"]
                else :
                    next_expiry = min(next_expiry, ses["expires"])
                #end if
            #end if
        #end for
        for sesid in to_delete :
            logger.info("timeout idle session %s" % repr(sesid))
            del sessions[sesid]
        #end for
        if next_expiry == None :
            break
        await asyncio.sleep(next_expiry - now)
    #end while
    # Nothing more to do for now, I will be restarted as necessary
    logger.debug("timeout_idle_sessions shutdown")
    timeout_idle_sessions_task = None
#end timeout_idle_sessions

async def handle_http(scope, receive, send, logger) :
    "handles an HTTP connection."
    global timeout_idle_sessions_task
    while True :
        event = await receive()
        logger.debug("http got event %s" % repr(event))
        if event["type"] == "http.request" :
            reply = io.BytesIO()
            out = io.TextIOWrapper(reply, encoding = "utf-8")
            session_id = get_cookies(scope).get("sessionid")
            if session_id != None and session_id not in sessions :
                logger.info \
                  (
                        "Connection from client %s with invalid session ID %s"
                    %
                        (repr(scope["client"]), repr(session_id))
                  )
                session_id = None
            #end if
            if session_id == None :
                while True :
                    session_id = "".join \
                      (
                        chr(c + ord("0"))
                        for c in random.choices(list(range(10)), k = 10)
                      )
                    if session_id not in sessions :
                        break
                #end while
                sessions[session_id] = {"count" : 0}
                if timeout_idle_sessions_task == None :
                    timeout_idle_sessions_task = asyncio.create_task(timeout_idle_sessions(logger))
                #end if
                logger.info \
                  (
                        "Connection from client %s, assigning session ID %s"
                    %
                        (repr(scope["client"]), repr(session_id))
                  )
            else :
                logger.info \
                  (
                        "Reconnection from client %s with existing session ID %s"
                    %
                        (repr(scope["client"]), repr(session_id))
                  )
            #end if
            sessions[session_id]["expires"] = round(time.time() + SESSION_TIMEOUT)
            out.write \
              (
                    "<!doctype html>\n"
                    "<head>\n"
                    "<title>Page Closure Detection Demo</title>\n"
                    "</head>\n"
                    "<body>\n"
                    "<script type=\"module\">\n"
                    "let conn = null\n"
                    "\n"
                    "function send_ping()\n"
                    "  {\n"
                    "    conn.send(\"pingy-pingy\")\n"
                    "  } /*send_ping*/\n"
                    "\n"
                    "function start_ping()\n"
                    "  {\n"
                    "    conn = new WebSocket(\"ws://%(server_addr)s:%(server_port)d/r_u_thair\")\n"
                    "      /* actually path part of URL is ignored by my Python code */\n"
                    "    conn.onopen = send_ping\n"
                    "    conn.onmessage =\n"
                    "        function (evt)\n"
                    "          {\n"
                    "            console.log(\"ping response = \", evt.data)\n"
                    "            setTimeout(send_ping, %(ping_ms)d)\n"
                    "          } /*function*/\n"
                    "  /* don’t bother with conn.onclose */\n"
                    "  } /*start_ping*/\n"
                    "\n"
                    "start_ping()\n"
                    "</script>\n"
                    "<p>Hello session %(session_id)s, feel free to close this"
                        " very boring page at any time.\n"
                    "</body>\n"
                    "</html>\n"
                %
                    {
                        "session_id" : html.escape(session_id),
                        "server_addr" : scope["server"][0],
                        "server_port" : scope["server"][1],
                        "ping_ms" : round(MSG_INTERVAL * 1000),
                    }
              )
            out.flush()
            reply = reply.getvalue()
            await send \
              (
                {
                    "type" : "http.response.start",
                    "status" : 200,
                    "headers" :
                        [
                            ["content-type", "text/html; charset=utf-8"],
                            ["content-length", "%d" % len(reply)],
                            ["set-cookie",
                                "sessionid=%(sessionid)s; SameSite=Lax; expires=%(expires)s"
                            %
                                {
                                    "sessionid" : session_id,
                                    "expires" :
                                        time.strftime
                                          (
                                            "%a, %d-%b-%Y %H:%M:%S UTC",
                                            time.gmtime(sessions[session_id]["expires"])
                                          ),
                                }
                            ]
                        ],
                }
              )
            await send({"type" : "http.response.body", "body" : reply})
        elif event["type"] == "http.disconnect" :
            logger.info("http disconnect")
            break
        else :
            logger.warning("unexpected http event type %s" % repr(event["type"]))
        #end if
    #end while
#end handle_http

async def handle_websocket(scope, receive, send, logger) :
    "handles a WebSocket connection."
    session_id = None
    while True :
        event = await receive()
        logger.debug("websocket got event %s" % repr(event))
        if session_id != None and session_id not in sessions :
            logger.warning("websocket session ID %s has timed out!" % repr(session_id))
            await send \
              (
                {
                    "type" : "websocket.close",
                    "code" : WEBSOCK_CLOSE.GOING_AWAY, # How do I say “timed out”?
                }
              )
            break
        #end if
        if event["type"] == "websocket.connect" :
            session_id = get_cookies(scope).get("sessionid")
            if session_id and session_id not in sessions :
                logger.warning("websocket invalid session ID %s" % repr(session_id))
                session_id = None
            #end if
            if session_id != None :
                sessions[session_id]["count"] += 1
                logger.info \
                  (
                        "websocket got session ID %s, conn count = %d"
                    %
                        (repr(session_id), sessions[session_id]["count"])
                  )
                await send({"type" : "websocket.accept"})
            else :
                logger.warning("websocket no session ID cookie found")
                await send \
                  (
                    {
                        "type" : "websocket.close",
                        "code" : WEBSOCK_CLOSE.POLICY_VIOLATION,
                    }
                  )
            #end if
        elif event["type"] == "websocket.receive" :
            # just echo back the data received
            reply = {"type" : "websocket.send"}
            for key in ("text", "bytes") :
                if key in event :
                    reply[key] = event[key]
                #end if
            #end for
            await send(reply)
            logger.info("websocket ping")
        elif event["type"] == "websocket.disconnect" :
            logger.info("websocket session ID %s disconnect code %d" % (repr(session_id), event["code"]))
            if session_id in sessions :
                sessions[session_id]["count"] -= 1
                logger.info \
                  (
                        "websocket session ID %s conn count = %d"
                    %
                        (repr(session_id), sessions[session_id]["count"])
                  )
            #end if
            break
        else :
            logger.warning("websocket unexpected event type %s" % repr(event["type"]))
        #end if
    #end while
#end handle_websocket

async def main(scope, receive, send) :
    logger = get_logger()
    logger.debug("connection scope = %s" % repr(scope))
    if scope["type"] == "http" :
        await handle_http(scope, receive, send, logger)
    elif scope["type"] == "websocket" :
        await handle_websocket(scope, receive, send, logger)
    else :
        raise AssertionError \
          (
            "unrecognized scope type %s, must be “http” or “websocket”" % scope["type"]
          )
    #end if
#end main
